import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { AiFollowUpThread } from '../components/ai/AiFollowUpThread.tsx';
import { IncidentHistoryDetail } from '../components/alerts/IncidentHistoryDetail.tsx';
import * as aiApi from '../api/ai.ts';
import { ApiError } from '../api/client.ts';
import { AiConversationMessage, AiFollowUpResponse, AiFollowUpStreamEvent, AlertHistoryItem } from '../types.ts';

const userMessage = (id: number, content: string): AiConversationMessage => ({
  id,
  role: 'user',
  content,
  tokens_used: 0,
  model: 'gemini-3.7-flash',
  created_at: '2026-10-14T10:00:00Z',
});

const assistantMessage = (id: number, content: string, tokens = 120): AiConversationMessage => ({
  id,
  role: 'assistant',
  content,
  tokens_used: tokens,
  model: 'gemini-3.7-flash',
  created_at: '2026-10-14T10:00:00Z',
});

const followUpResponse = (message: AiConversationMessage, turnCount: number): AiFollowUpResponse => ({
  message,
  tokens_in: 100,
  tokens_out: 20,
  tokens_thoughts: 0,
  tokens_used: message.tokens_used,
  model_used: message.model,
  fallback_used: false,
  fallback_attempts: [],
  turn_count: turnCount,
  max_turns: 10,
});

describe('AiFollowUpThread', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('loads the existing thread with turn counter and token tally', async () => {
    vi.spyOn(aiApi, 'fetchConversationMessages').mockResolvedValue({
      audit_id: 7,
      messages: [userMessage(1, 'Which port does dnsmasq use?'), assistantMessage(2, 'It listens on **port 53**.', 150)],
      turn_count: 1,
      max_turns: 10,
    });

    render(<AiFollowUpThread auditId={7} />);

    expect(await screen.findByText('Which port does dnsmasq use?')).toBeInTheDocument();
    expect(screen.getByText('port 53')).toBeInTheDocument();
    expect(screen.getByText('1 / 10 turns')).toBeInTheDocument();
    expect(screen.getByText('150')).toBeInTheDocument();
    expect(aiApi.fetchConversationMessages).toHaveBeenCalledWith(7);
  });

  it('streams a reply on Ctrl+Enter and reloads the saved thread', async () => {
    const fetchSpy = vi
      .spyOn(aiApi, 'fetchConversationMessages')
      .mockResolvedValueOnce({ audit_id: 7, messages: [], turn_count: 0, max_turns: 10 })
      .mockResolvedValueOnce({
        audit_id: 7,
        messages: [userMessage(1, 'How do I test DNS?'), assistantMessage(2, 'Run dig against 1.1.1.1.')],
        turn_count: 1,
        max_turns: 10,
      });

    let emit: (evt: AiFollowUpStreamEvent) => void = () => {};
    let finish: (res: AiFollowUpResponse) => void = () => {};
    const sendSpy = vi.spyOn(aiApi, 'sendAiFollowUp').mockImplementation((_id, _req, onEvent) => {
      emit = onEvent!;
      return new Promise((resolve) => {
        finish = resolve;
      });
    });

    render(<AiFollowUpThread auditId={7} provider="gemini" model="gemini-3.7-flash" />);

    const input = await screen.findByLabelText('Follow-up question');
    fireEvent.change(input, { target: { value: '  How do I test DNS?  ' } });
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });

    expect(sendSpy).toHaveBeenCalledWith(
      7,
      { message: 'How do I test DNS?', provider: 'gemini', model: 'gemini-3.7-flash' },
      expect.any(Function),
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    );
    expect(screen.getByText('How do I test DNS?')).toBeInTheDocument();
    expect(screen.getByText('Waiting for reply...')).toBeInTheDocument();

    act(() => {
      emit({ stage: 'calling', model: 'gemini-3.7-flash' });
      emit({ stage: 'delta', content: 'Run dig ' });
      emit({ stage: 'delta', content: 'now.' });
    });
    expect(screen.getByText('Run dig now.')).toBeInTheDocument();

    await act(async () => {
      finish(followUpResponse(assistantMessage(2, 'Run dig against 1.1.1.1.'), 1));
    });

    expect(await screen.findByText('Run dig against 1.1.1.1.')).toBeInTheDocument();
    expect(screen.getByText('1 / 10 turns')).toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect((screen.getByLabelText('Follow-up question') as HTMLTextAreaElement).value).toBe('');
  });

  it('discards partial text when the stream fails over to a fallback model', async () => {
    vi.spyOn(aiApi, 'fetchConversationMessages').mockResolvedValue({ audit_id: 7, messages: [], turn_count: 0, max_turns: 10 });
    let emit: (evt: AiFollowUpStreamEvent) => void = () => {};
    vi.spyOn(aiApi, 'sendAiFollowUp').mockImplementation((_id, _req, onEvent) => {
      emit = onEvent!;
      return new Promise(() => {});
    });

    render(<AiFollowUpThread auditId={7} />);
    const input = await screen.findByLabelText('Follow-up question');
    fireEvent.change(input, { target: { value: 'q' } });
    fireEvent.click(screen.getByTitle('Send (Ctrl+Enter or Cmd+Enter)'));

    act(() => {
      emit({ stage: 'delta', content: 'Partial answer' });
      emit({ stage: 'failover', failed_model: 'model-a', next_model: 'model-b' });
    });
    expect(screen.queryByText('Partial answer')).not.toBeInTheDocument();
    expect(screen.getByText('model-a failed. Failing over to model-b...')).toBeInTheDocument();
  });

  it('shows the error and restores the question when sending fails', async () => {
    vi.spyOn(aiApi, 'fetchConversationMessages').mockResolvedValue({ audit_id: 7, messages: [], turn_count: 0, max_turns: 10 });
    vi.spyOn(aiApi, 'sendAiFollowUp').mockRejectedValue(
      new ApiError(429, 'Rate limit exceeded. Maximum 10 AI diagnosis requests per minute.')
    );

    render(<AiFollowUpThread auditId={7} />);
    const input = await screen.findByLabelText('Follow-up question');
    fireEvent.change(input, { target: { value: 'Why port 53?' } });
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true });

    expect(await screen.findByText(/Rate limit exceeded/)).toBeInTheDocument();
    expect((screen.getByLabelText('Follow-up question') as HTMLTextAreaElement).value).toBe('Why port 53?');
  });

  it('reports sending state and aborts a streaming reply when unmounted', async () => {
    vi.spyOn(aiApi, 'fetchConversationMessages').mockResolvedValue({ audit_id: 7, messages: [], turn_count: 0, max_turns: 10 });
    let signal: AbortSignal | undefined;
    vi.spyOn(aiApi, 'sendAiFollowUp').mockImplementation((_id, _req, _onEvent, options) => {
      signal = options?.signal;
      return new Promise(() => {});
    });
    const onSendingChange = vi.fn();

    const { unmount } = render(<AiFollowUpThread auditId={7} onSendingChange={onSendingChange} />);
    const input = await screen.findByLabelText('Follow-up question');
    fireEvent.change(input, { target: { value: 'q' } });
    fireEvent.click(screen.getByTitle('Send (Ctrl+Enter or Cmd+Enter)'));

    await waitFor(() => expect(onSendingChange).toHaveBeenLastCalledWith(true));
    expect(signal?.aborted).toBe(false);

    unmount();
    expect(signal?.aborted).toBe(true);
    expect(onSendingChange).toHaveBeenLastCalledWith(false);
  });

  it('reports sending as finished once the reply settles', async () => {
    vi.spyOn(aiApi, 'fetchConversationMessages').mockResolvedValue({ audit_id: 7, messages: [], turn_count: 0, max_turns: 10 });
    vi.spyOn(aiApi, 'sendAiFollowUp').mockRejectedValue(new Error('Request timed out'));
    const onSendingChange = vi.fn();

    render(<AiFollowUpThread auditId={7} onSendingChange={onSendingChange} />);
    const input = await screen.findByLabelText('Follow-up question');
    fireEvent.change(input, { target: { value: 'q' } });
    fireEvent.click(screen.getByTitle('Send (Ctrl+Enter or Cmd+Enter)'));

    expect(await screen.findByText('Request timed out')).toBeInTheDocument();
    expect(onSendingChange).toHaveBeenCalledWith(true);
    expect(onSendingChange).toHaveBeenLastCalledWith(false);
  });

  it('does not send on plain Enter or with an empty question', async () => {
    vi.spyOn(aiApi, 'fetchConversationMessages').mockResolvedValue({ audit_id: 7, messages: [], turn_count: 0, max_turns: 10 });
    const sendSpy = vi.spyOn(aiApi, 'sendAiFollowUp');

    render(<AiFollowUpThread auditId={7} />);
    const input = await screen.findByLabelText('Follow-up question');
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true });
    fireEvent.change(input, { target: { value: 'question' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(sendSpy).not.toHaveBeenCalled();
    expect(screen.getByTitle('Send (Ctrl+Enter or Cmd+Enter)')).not.toBeDisabled();
  });

  it('disables input with an advisory once the turn limit is reached', async () => {
    const messages: AiConversationMessage[] = [];
    for (let i = 0; i < 10; i++) {
      messages.push(userMessage(i * 2 + 1, `q${i}`), assistantMessage(i * 2 + 2, `a${i}`));
    }
    vi.spyOn(aiApi, 'fetchConversationMessages').mockResolvedValue({ audit_id: 7, messages, turn_count: 10, max_turns: 10 });

    render(<AiFollowUpThread auditId={7} />);

    expect(await screen.findByText('Turn limit reached.')).toBeInTheDocument();
    expect(screen.getByText('10 / 10 turns')).toBeInTheDocument();
    expect(screen.queryByLabelText('Follow-up question')).not.toBeInTheDocument();
  });

  it('clears the thread after confirmation', async () => {
    vi.spyOn(aiApi, 'fetchConversationMessages').mockResolvedValue({
      audit_id: 7,
      messages: [userMessage(1, 'q1'), assistantMessage(2, 'a1')],
      turn_count: 1,
      max_turns: 10,
    });
    const clearSpy = vi.spyOn(aiApi, 'clearConversationThread').mockResolvedValue({ status: 'ok' });

    render(<AiFollowUpThread auditId={7} />);
    fireEvent.click(await screen.findByText('Clear Thread'));
    expect(clearSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('Confirm'));
    await waitFor(() => expect(clearSpy).toHaveBeenCalledWith(7));
    await waitFor(() => expect(screen.queryByText('q1')).not.toBeInTheDocument());
    expect(screen.getByText('0 / 10 turns')).toBeInTheDocument();
  });

  it('shows a retry action when the thread fails to load', async () => {
    const fetchSpy = vi
      .spyOn(aiApi, 'fetchConversationMessages')
      .mockRejectedValueOnce(new Error('AI analysis 7 not found.'))
      .mockResolvedValueOnce({ audit_id: 7, messages: [], turn_count: 0, max_turns: 10 });

    render(<AiFollowUpThread auditId={7} />);
    expect(await screen.findByText('AI analysis 7 not found.')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Retry'));
    expect(await screen.findByLabelText('Follow-up question')).toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });
});

describe('sendAiFollowUp stream parsing', () => {
  const encoder = new TextEncoder();

  const streamResponse = (chunks: string[]) => {
    let index = 0;
    return {
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () =>
            index < chunks.length ? { done: false, value: encoder.encode(chunks[index++]) } : { done: true, value: undefined },
          cancel: async () => {},
        }),
      },
    } as unknown as Response;
  };

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('forwards events across chunk boundaries and resolves with the complete result', async () => {
    const result = followUpResponse(assistantMessage(2, 'Done.'), 1);
    const complete = `data: ${JSON.stringify({ stage: 'complete', result })}\n\n`;
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse([
        'data: {"stage": "init", "model": "m"}\n\ndata: {"stage": "del',
        'ta", "content": "Do"}\n\n',
        complete,
      ])
    );
    vi.stubGlobal('fetch', fetchMock);

    const events: AiFollowUpStreamEvent[] = [];
    const res = await aiApi.sendAiFollowUp(7, { message: 'q' }, (evt) => events.push(evt));

    expect(res).toEqual(result);
    expect(events.map((e) => e.stage)).toEqual(['init', 'delta', 'complete']);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/ai/audit/7/follow-up/stream',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ message: 'q' }) })
    );
  });

  it('rejects with the error event message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse(['data: {"stage": "error", "message": "Request timed out"}\n\n'])));
    await expect(aiApi.sendAiFollowUp(7, { message: 'q' }, () => {})).rejects.toThrow('Request timed out');
  });

  it('rejects with the API detail when the request is refused', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        text: async () => JSON.stringify({ detail: 'Conversation turn limit of 10 reached.' }),
      })
    );
    await expect(aiApi.sendAiFollowUp(7, { message: 'q' }, () => {})).rejects.toMatchObject({
      status: 400,
      message: 'Conversation turn limit of 10 reached.',
    });
  });
});

describe('IncidentHistoryDetail follow-up discussion', () => {
  const baseItem: AlertHistoryItem = {
    id: 3,
    rule_id: null,
    rule_name: 'On-Demand Analysis',
    channel_id: null,
    trigger_count: 2,
    sample_log: null,
    incident_summary: '## Summary\nDNS failure.',
    ai_enrichment: true,
    ai_model: 'gemini-3.7-flash',
    ai_audit_id: 42,
    triggered_at: '2026-10-06T14:38:53Z',
  };

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('loads the thread only when the section is first expanded', async () => {
    const fetchSpy = vi
      .spyOn(aiApi, 'fetchConversationMessages')
      .mockResolvedValue({ audit_id: 42, messages: [], turn_count: 0, max_turns: 10 });

    render(<IncidentHistoryDetail item={baseItem} />);
    const toggle = screen.getByRole('button', { name: /Follow-up Discussion/i });
    expect(fetchSpy).not.toHaveBeenCalled();

    fireEvent.click(toggle);
    expect(await screen.findByLabelText('Follow-up question')).toBeInTheDocument();
    expect(fetchSpy).toHaveBeenCalledWith(42);

    // Collapsing and expanding again keeps the loaded thread
    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('is not offered without an AI audit record or for failed analyses', () => {
    const { rerender } = render(<IncidentHistoryDetail item={{ ...baseItem, ai_audit_id: null }} />);
    expect(screen.queryByText('Follow-up Discussion')).not.toBeInTheDocument();

    rerender(<IncidentHistoryDetail item={{ ...baseItem, incident_summary: 'AI analysis failed: timeout' }} />);
    expect(screen.queryByText('Follow-up Discussion')).not.toBeInTheDocument();
  });
});
