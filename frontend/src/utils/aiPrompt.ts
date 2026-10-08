/**
 * Shared AI prompt constants and helper functions.
 */

export const DEFAULT_AI_MODEL = 'gemini-3.7-flash';

export const DEFAULT_SYSTEM_PROMPT = `You are an expert systems engineer, site reliability engineer (SRE), and Linux/Docker administrator.
Review the following redacted server/container logs and provide a structured diagnosis in Markdown format.

Log timestamps cite both UTC and the operator homelab local time (for example, 01:05:00 UTC (02:05:00 local)). When referencing specific event times or advising the operator to inspect logs on host systems, cite both the UTC time and the corresponding local time to avoid confusion across server and host timezones.

Your response MUST include the following three sections with exact headers:
## Summary
A concise 1-2 sentence overview of the issue.

## Root Cause
A detailed explanation of why the event or failure occurred based on the log evidence.

## Actionable Remediation
Step-by-step commands, configuration fixes, or debugging steps to resolve the issue.`;

export const SYSTEM_INSTRUCTIONS_HEADER = '=== SYSTEM INSTRUCTIONS ===';
export const USER_ANALYSIS_PROMPT_HEADER = '=== USER ANALYSIS PROMPT ===';

/**
 * Normalize prompt text by stripping Windows CRLF line endings and trimming leading/trailing whitespace.
 */
export function normalizePrompt(text: string | null | undefined): string {
  return (text || '').replace(/\r\n/g, '\n').trim();
}

/**
 * Assemble the complete prompt envelope for full LLM dispatch or copying.
 */
export function buildFullEnvelope(systemPrompt: string, userPrompt: string): string {
  const cleanSys = normalizePrompt(systemPrompt || DEFAULT_SYSTEM_PROMPT);
  const cleanUser = normalizePrompt(userPrompt || '');
  return `${SYSTEM_INSTRUCTIONS_HEADER}\n${cleanSys}\n\n${USER_ANALYSIS_PROMPT_HEADER}\n${cleanUser}`;
}

/**
 * Parse an envelope back into system instructions and user analysis prompt.
 */
export function parseFullEnvelope(
  fullText: string,
  fallbackSystemPrompt: string
): { systemPrompt: string; userPrompt: string } {
  const sysIdx = fullText.indexOf(SYSTEM_INSTRUCTIONS_HEADER);
  const userIdx = fullText.indexOf(USER_ANALYSIS_PROMPT_HEADER);

  if (sysIdx !== -1 && userIdx !== -1 && userIdx > sysIdx) {
    const prefix = fullText.slice(0, sysIdx).trim();
    const sysBody = fullText.slice(sysIdx + SYSTEM_INSTRUCTIONS_HEADER.length, userIdx).trim();
    const sys = prefix ? (sysBody ? `${prefix}\n${sysBody}` : prefix) : sysBody;
    const user = fullText.slice(userIdx + USER_ANALYSIS_PROMPT_HEADER.length).trim();
    return {
      systemPrompt: sys || fallbackSystemPrompt,
      userPrompt: user,
    };
  }

  if (userIdx !== -1) {
    const sys = fullText.slice(0, userIdx).trim();
    const user = fullText.slice(userIdx + USER_ANALYSIS_PROMPT_HEADER.length).trim();
    return {
      systemPrompt: sys || fallbackSystemPrompt,
      userPrompt: user,
    };
  }

  // If user stripped the delimiter headers, treat the entire string as the analysis prompt
  return {
    systemPrompt: fallbackSystemPrompt,
    userPrompt: fullText.trim(),
  };
}

/**
 * Format a 1-based index into an ordinal string: 1 -> "1st", 2 -> "2nd", 3 -> "3rd", 4 -> "4th", etc.
 */
export function getOrdinalSuffix(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n}st`;
  if (mod10 === 2 && mod100 !== 12) return `${n}nd`;
  if (mod10 === 3 && mod100 !== 13) return `${n}rd`;
  return `${n}th`;
}

export const NON_TEXT_MODEL_KEYWORDS: string[] = [
  'transcribe',
  'transcription',
  'whisper',
  'audio',
  'speech',
  'voice',
  'tts',
  'stt',
  'realtime',
  'image',
  'imagen',
  'dall-e',
  'dalle',
  'flux',
  'diffusion',
  'midjourney',
  'veo',
  'video',
  'canvas',
  'embedding',
  'embed',
  'moderation',
  'rerank',
  'computer-use',
  'computer_use',
];

/**
 * Returns True if modelId is a text-generation/chat model, False if it is an audio/image/embedding model.
 */
export function isTextModel(modelId: string): boolean {
  const low = (modelId || '').toLowerCase();
  if (!low) return false;
  return !NON_TEXT_MODEL_KEYWORDS.some((kw) => low.includes(kw));
}


