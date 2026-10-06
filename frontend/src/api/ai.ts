import { apiFetch, ApiError } from './client.ts';
import {
  AiDiagnosisRequest,
  AiDiagnosisResponse,
  AiPreviewRequest,
  AiPreviewResponse,
  AiDiagnosisStreamEvent,
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

export async function getAiModels(provider?: string, refresh: boolean = false): Promise<AiModelsResponse> {
  const params = new URLSearchParams();
  if (provider) params.set('provider', provider);
  const qs = params.toString() ? `?${params.toString()}` : '';

  let res: AiModelsResponse;
  if (refresh) {
    res = await apiFetch<AiModelsResponse>(`/api/ai/models/refresh${qs}`, {
      method: 'POST',
    });
  } else {
    res = await apiFetch<AiModelsResponse>(`/api/ai/models${qs}`);
  }

  if (res && Array.isArray(res.models)) {
    res.models = res.models.filter((m) => isTextModel(m.id));
  }
  return res;
}


