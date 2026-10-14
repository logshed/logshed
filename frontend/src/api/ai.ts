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

export async function previewAiPrompt(req: AiPreviewRequest): Promise<AiPreviewResponse> {
  return apiFetch<AiPreviewResponse>('/api/ai/preview', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function diagnoseLogs(
  req: AiDiagnosisRequest,
  onEventCallback?: (event: AiDiagnosisStreamEvent) => void,
): Promise<AiDiagnosisResponse> {
  const { onEvent: reqOnEvent, ...payload } = req;
  const onEvent = onEventCallback || reqOnEvent;

  if (onEvent && typeof window !== 'undefined' && typeof fetch === 'function') {
    try {
      const response = await fetch('/api/ai/diagnose/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Requested-With': 'XMLHttpRequest',
        },
        credentials: 'include',
        body: JSON.stringify(payload),
      });

      if (response.ok && response.body) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let finalResult: AiDiagnosisResponse | null = null;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('data: ')) {
              let data: AiDiagnosisStreamEvent | null = null;
              try {
                data = JSON.parse(trimmed.slice(6));
                if (data) {
                  onEvent(data);
                  if (data.stage === 'complete' && data.result) {
                    finalResult = data.result;
                  } else if (data.stage === 'error') {
                    throw new Error(data.message || 'AI diagnosis failed');
                  }
                }
              } catch (e: any) {
                if (e.message === 'AI diagnosis failed' || data?.stage === 'error') {
                  throw e;
                }
              }
            }
          }
        }

        if (finalResult) {
          return finalResult;
        }
      }
    } catch (streamErr: any) {
      if (streamErr instanceof ApiError || streamErr.message === 'AI diagnosis failed') {
        throw streamErr;
      }
      // Fall through to standard apiFetch on stream failure
    }
  }

  return apiFetch<AiDiagnosisResponse>('/api/ai/diagnose', {
    method: 'POST',
    body: JSON.stringify(payload),
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
 * Request errors (turn limit, rate limit, validation) reject with an ApiError.
 */
export async function sendAiFollowUp(
  auditId: number,
  req: AiFollowUpRequest,
  onEvent?: (event: AiFollowUpStreamEvent) => void,
): Promise<AiFollowUpResponse> {
  const endpoint = `/api/ai/audit/${auditId}/follow-up`;
  if (!onEvent) {
    return apiFetch<AiFollowUpResponse>(endpoint, {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  const streamEndpoint = `${endpoint}/stream`;
  const response = await fetch(streamEndpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
    },
    credentials: 'include',
    body: JSON.stringify(req),
  });
  if (!response.ok) {
    throw await toApiError(response, streamEndpoint);
  }
  if (!response.body) {
    throw new Error('Streaming is not supported by this browser.');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';

    for (const frame of frames) {
      const trimmed = frame.trim();
      if (!trimmed.startsWith('data: ')) continue;
      let event: AiFollowUpStreamEvent;
      try {
        event = JSON.parse(trimmed.slice(6));
      } catch {
        continue;
      }
      onEvent(event);
      if (event.stage === 'complete' && event.result) {
        await reader.cancel();
        return event.result;
      }
      if (event.stage === 'error') {
        await reader.cancel();
        throw new Error(event.message || 'AI follow-up failed.');
      }
    }
  }

  throw new Error('The AI follow-up stream ended before a reply was received.');
}
