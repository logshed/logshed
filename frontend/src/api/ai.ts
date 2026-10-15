import { apiFetch, ApiError, toApiError } from './client.ts';
import {
  AiConversationHistoryResponse,
  AiDiagnosisRequest,
  AiDiagnosisResponse,
  AiPreviewRequest,
  AiPreviewResponse,
  AiDiagnosisStreamEvent,
  AiFollowUpRequest,
  AiFollowUpResponse,
  AiFollowUpStreamEvent,
  AiModelsResponse,
} from '../types.ts';
import { isTextModel } from '../utils/aiPrompt.ts';

/** Options shared by the AI request helpers. Aborting `signal` cancels the request or stream. */
export interface AiRequestOptions {
  signal?: AbortSignal;
}

/** An AI stream reported an `error` event, or ended before delivering its result. */
export class AiStreamError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AiStreamError';
  }
}

/** True for the rejection produced by aborting a fetch or stream read. */
export function isAbortError(err: unknown): boolean {
  return (err as { name?: string } | null)?.name === 'AbortError';
}

/** Parse one SSE frame into its JSON payload. Comment lines (`: ping`) and malformed frames yield null. */
function parseSseFrame<T>(frame: string): T | null {
  const data = frame
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(line.startsWith('data: ') ? 6 : 5))
    .join('\n');
  if (!data) return null;
  try {
    return JSON.parse(data) as T;
  } catch {
    return null;
  }
}

/** Yield the JSON events of an SSE response body. The reader is cancelled once iteration stops. */
async function* readSseEvents<T>(body: ReadableStream<Uint8Array>): AsyncGenerator<T> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split('\n\n');
      buffer = frames.pop() || '';

      for (const frame of frames) {
        const event = parseSseFrame<T>(frame);
        if (event) yield event;
      }
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}

/** POST a JSON body to an SSE endpoint and return the response, rejecting with an ApiError when it is refused. */
async function postStream(endpoint: string, payload: unknown, signal?: AbortSignal): Promise<Response> {
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
    },
    credentials: 'include',
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    throw await toApiError(response, endpoint);
  }
  return response;
}

export async function previewAiPrompt(
  req: AiPreviewRequest,
  options: AiRequestOptions = {},
): Promise<AiPreviewResponse> {
  return apiFetch<AiPreviewResponse>('/api/ai/preview', {
    method: 'POST',
    body: JSON.stringify(req),
    signal: options.signal,
  });
}

/**
 * Run an AI diagnosis. With an `onEvent` callback, progress is streamed over SSE.
 * The analysis is never run twice: the non-streaming endpoint is used only when the stream
 * request could not be sent at all or the browser cannot read a streamed body. A refused request
 * rejects with an ApiError; an `error` event, or a stream that ends without a result, rejects
 * with an AiStreamError.
 */
export async function diagnoseLogs(
  req: AiDiagnosisRequest,
  onEventCallback?: (event: AiDiagnosisStreamEvent) => void,
  options: AiRequestOptions = {},
): Promise<AiDiagnosisResponse> {
  const { onEvent: reqOnEvent, ...payload } = req;
  const onEvent = onEventCallback || reqOnEvent;
  const { signal } = options;

  if (onEvent && typeof window !== 'undefined' && typeof fetch === 'function') {
    const streamEndpoint = '/api/ai/diagnose/stream';
    let response: Response | null = null;
    try {
      response = await postStream(streamEndpoint, payload, signal);
    } catch (err) {
      if (err instanceof ApiError || isAbortError(err)) throw err;
      // The stream request never reached a response; use the standard endpoint below
    }

    if (response?.body) {
      for await (const event of readSseEvents<AiDiagnosisStreamEvent>(response.body)) {
        onEvent(event);
        if (event.stage === 'complete' && event.result) {
          return event.result;
        }
        if (event.stage === 'error') {
          throw new AiStreamError(event.message || 'AI diagnosis failed.');
        }
      }
      throw new AiStreamError('The AI diagnosis stream ended before a result was received.');
    }
  }

  return apiFetch<AiDiagnosisResponse>('/api/ai/diagnose', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  });
}

export async function getAiModels(
  provider?: string,
  refresh: boolean = false,
  apiKey?: string,
  baseUrl?: string,
): Promise<AiModelsResponse> {
  const params = new URLSearchParams();
  if (provider) params.set('provider', provider);
  const qs = params.toString() ? `?${params.toString()}` : '';

  let res: AiModelsResponse;
  if (refresh) {
    const payload: Record<string, string> = {};
    if (apiKey && apiKey !== '********' && apiKey.trim()) {
      payload.api_key = apiKey.trim();
    }
    if (baseUrl && baseUrl.trim()) {
      payload.base_url = baseUrl.trim();
    }
    res = await apiFetch<AiModelsResponse>(`/api/ai/models/refresh${qs}`, {
      method: 'POST',
      body: Object.keys(payload).length > 0 ? JSON.stringify(payload) : undefined,
    });
  } else {
    res = await apiFetch<AiModelsResponse>(`/api/ai/models${qs}`);
  }

  if (res && Array.isArray(res.models)) {
    res.models = res.models.filter((m) => isTextModel(m.id));
  }
  return res;
}



export async function fetchConversationMessages(auditId: number): Promise<AiConversationHistoryResponse> {
  return apiFetch<AiConversationHistoryResponse>(`/api/ai/audit/${auditId}/messages`);
}

export async function clearConversationThread(auditId: number): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/api/ai/audit/${auditId}/messages`, { method: 'DELETE' });
}

/**
 * Ask a follow-up question about an AI analysis. With `onEvent`, the reply is streamed over SSE
 * (`delta` events carry text chunks); without it the synchronous endpoint is used.
 * Request errors (turn limit, rate limit, validation) reject with an ApiError; an `error` event
 * or a stream that ends without a reply rejects with an AiStreamError.
 */
export async function sendAiFollowUp(
  auditId: number,
  req: AiFollowUpRequest,
  onEvent?: (event: AiFollowUpStreamEvent) => void,
  options: AiRequestOptions = {},
): Promise<AiFollowUpResponse> {
  const endpoint = `/api/ai/audit/${auditId}/follow-up`;
  if (!onEvent) {
    return apiFetch<AiFollowUpResponse>(endpoint, {
      method: 'POST',
      body: JSON.stringify(req),
      signal: options.signal,
    });
  }

  const response = await postStream(`${endpoint}/stream`, req, options.signal);
  if (!response.body) {
    throw new Error('Streaming is not supported by this browser.');
  }

  for await (const event of readSseEvents<AiFollowUpStreamEvent>(response.body)) {
    onEvent(event);
    if (event.stage === 'complete' && event.result) {
      return event.result;
    }
    if (event.stage === 'error') {
      throw new AiStreamError(event.message || 'AI follow-up failed.');
    }
  }

  throw new AiStreamError('The AI follow-up stream ended before a reply was received.');
}
