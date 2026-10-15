import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import * as aiApi from '../api/ai.ts';
import { ApiError } from '../api/client.ts';
import { AiDiagnosisResponse, AiDiagnosisStreamEvent } from '../types.ts';

const encoder = new TextEncoder();

const diagnosis: AiDiagnosisResponse = {
  summary: 'DNS failure.',
  root_cause: 'Upstream unreachable.',
  remediation: 'Check port 53.',
  model_used: 'gemini-3.7-flash',
  tokens_in: 100,
  tokens_out: 20,
  tokens_thoughts: 0,
  tokens_used: 120,
  audit_id: 9,
};

const frame = (event: object) => `data: ${JSON.stringify(event)}\n\n`;

/** A stream response that delivers the given chunks, then optionally fails the next read. */
const streamResponse = (chunks: string[], failAfter?: Error) => {
  let index = 0;
  const cancel = vi.fn(async () => {});
  const response = {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (index < chunks.length) return { done: false, value: encoder.encode(chunks[index++]) };
          if (failAfter) throw failAfter;
          return { done: true, value: undefined };
        },
        cancel,
      }),
    },
  } as unknown as Response;
  return { response, cancel };
};

const jsonResponse = (status: number, data: unknown) =>
  ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
  }) as unknown as Response;

const request = { log_ids: [1, 2] };

describe('diagnoseLogs streaming and fallback', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('resolves with the complete result over the stream', async () => {
    const { response } = streamResponse([
      frame({ stage: 'init', model: 'm' }),
      frame({ stage: 'complete', result: diagnosis }),
    ]);
    const fetchMock = vi.fn().mockResolvedValue(response);
    vi.stubGlobal('fetch', fetchMock);

    const events: AiDiagnosisStreamEvent[] = [];
    await expect(aiApi.diagnoseLogs(request, (evt) => events.push(evt))).resolves.toEqual(diagnosis);
    expect(events.map((e) => e.stage)).toEqual(['init', 'complete']);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('throws the error frame message without re-running the analysis', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse([
        frame({ stage: 'init', model: 'm' }),
        frame({ stage: 'error', message: 'Gemini API error: 400 INVALID_ARGUMENT' }),
      ]).response
    );
    vi.stubGlobal('fetch', fetchMock);

    const err = await aiApi.diagnoseLogs(request, () => {}).catch((e) => e);
    expect(err).toBeInstanceOf(aiApi.AiStreamError);
    expect(err.message).toBe('Gemini API error: 400 INVALID_ARGUMENT');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('throws an ApiError on a refused stream request without falling back', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(429, { detail: 'Rate limit exceeded. Maximum 10 AI diagnosis requests per minute.' })
    );
    vi.stubGlobal('fetch', fetchMock);

    const err = await aiApi.diagnoseLogs(request, () => {}).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(429);
    expect(err.message).toMatch(/Rate limit exceeded/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('dispatches the unauthorized event when the stream request returns 401', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'Not authenticated' }));
    vi.stubGlobal('fetch', fetchMock);
    const listener = vi.fn();
    window.addEventListener('logshed:unauthorized', listener);
    try {
      await expect(aiApi.diagnoseLogs(request, () => {})).rejects.toBeInstanceOf(ApiError);
    } finally {
      window.removeEventListener('logshed:unauthorized', listener);
    }
    expect(listener).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('falls back once to the standard endpoint when the response has no body', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, body: null } as unknown as Response)
      .mockResolvedValueOnce(jsonResponse(200, diagnosis));
    vi.stubGlobal('fetch', fetchMock);

    await expect(aiApi.diagnoseLogs(request, () => {})).resolves.toEqual(diagnosis);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe('/api/ai/diagnose');
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST', body: JSON.stringify(request) });
  });

  it('falls back once when the stream request fails before any frame arrives', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse(200, diagnosis));
    vi.stubGlobal('fetch', fetchMock);

    await expect(aiApi.diagnoseLogs(request, () => {})).resolves.toEqual(diagnosis);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe('/api/ai/diagnose');
  });

  it('treats a stream that ends without a result as an error, not a fallback', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse([frame({ stage: 'init', model: 'm' }), frame({ stage: 'calling', model: 'm' })]).response
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(aiApi.diagnoseLogs(request, () => {})).rejects.toBeInstanceOf(aiApi.AiStreamError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('treats a connection drop after the first frame as an error, not a fallback', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse([frame({ stage: 'init', model: 'm' })], new TypeError('network error')).response
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(aiApi.diagnoseLogs(request, () => {})).rejects.toThrow('network error');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('ignores SSE comment pings between frames', async () => {
    const { response, cancel } = streamResponse([
      frame({ stage: 'init', model: 'm' }),
      ': ping\n\n',
      ': ping\n\ndata: {"stage": "calling", "model": "m"}\n\n',
      frame({ stage: 'complete', result: diagnosis }),
    ]);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));

    const events: AiDiagnosisStreamEvent[] = [];
    await expect(aiApi.diagnoseLogs(request, (evt) => events.push(evt))).resolves.toEqual(diagnosis);
    expect(events.map((e) => e.stage)).toEqual(['init', 'calling', 'complete']);
    expect(cancel).toHaveBeenCalled();
  });

  it('forwards the abort signal and does not fall back when aborted', async () => {
    const controller = new AbortController();
    const abortError = new DOMException('The operation was aborted.', 'AbortError');
    const fetchMock = vi.fn().mockRejectedValue(abortError);
    vi.stubGlobal('fetch', fetchMock);

    controller.abort();
    await expect(aiApi.diagnoseLogs(request, () => {}, { signal: controller.signal })).rejects.toBe(abortError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it('forwards the abort signal on the non-streaming path', async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, diagnosis));
    vi.stubGlobal('fetch', fetchMock);

    await aiApi.diagnoseLogs(request, undefined, { signal: controller.signal });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/ai/diagnose');
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });
});

describe('previewAiPrompt', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('forwards the abort signal to fetch', async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { redacted_prompt: 'p' }));
    vi.stubGlobal('fetch', fetchMock);

    await aiApi.previewAiPrompt({ log_ids: [1] }, { signal: controller.signal });
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });
});

describe('sendAiFollowUp stream comments and abort', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('ignores SSE comment pings', async () => {
    const result = { message: { id: 2 }, model_used: 'm' };
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([': ping\n\n', frame({ stage: 'delta', content: 'Hi' }), frame({ stage: 'complete', result })])
          .response
      )
    );
    const stages: string[] = [];
    await expect(aiApi.sendAiFollowUp(7, { message: 'q' }, (evt) => stages.push(evt.stage))).resolves.toEqual(result);
    expect(stages).toEqual(['delta', 'complete']);
  });

  it('forwards the abort signal to fetch', async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse([frame({ stage: 'complete', result: { message: { id: 2 } } })]).response
    );
    vi.stubGlobal('fetch', fetchMock);

    await aiApi.sendAiFollowUp(7, { message: 'q' }, () => {}, { signal: controller.signal });
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });
});
