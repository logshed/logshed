import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, Info, RefreshCw, Send, Trash2 } from 'lucide-react';
import { AiConversationMessage } from '../../types.ts';
import { clearConversationThread, fetchConversationMessages, sendAiFollowUp } from '../../api/ai.ts';
import { MarkdownRenderer } from '../common/MarkdownRenderer.tsx';
import { LoadingSpinner } from '../common/LoadingSpinner.tsx';

const MAX_QUESTION_LENGTH = 2000;
const DEFAULT_MAX_TURNS = 10;
const STICK_TO_BOTTOM_THRESHOLD_PX = 40;

interface AiFollowUpThreadProps {
  auditId: number;
  /** Optional provider override; the active provider in Settings is used when omitted. */
  provider?: string;
  /** Optional model override; the provider's configured model is used when omitted. */
  model?: string;
  /** Optional fallback chain override (an empty list means none); the provider's configured fallbacks are used when omitted. */
  fallbackModels?: string[];
  /** Called with true while a follow-up reply is being sent or streamed, and false once it settles. */
  onSendingChange?: (sending: boolean) => void;
}

export const AiFollowUpThread: React.FC<AiFollowUpThreadProps> = ({ auditId, provider, model, fallbackModels, onSendingChange }) => {
  const [messages, setMessages] = useState<AiConversationMessage[]>([]);
  const [maxTurns, setMaxTurns] = useState<number>(DEFAULT_MAX_TURNS);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [draft, setDraft] = useState<string>('');
  const [isSending, setIsSending] = useState<boolean>(false);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [streamText, setStreamText] = useState<string>('');
  const [streamStatus, setStreamStatus] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  const [confirmClear, setConfirmClear] = useState<boolean>(false);
  const [isClearing, setIsClearing] = useState<boolean>(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef<boolean>(true);
  const sendAbortRef = useRef<AbortController | null>(null);
  const onSendingChangeRef = useRef(onSendingChange);

  useEffect(() => {
    onSendingChangeRef.current = onSendingChange;
  });

  useEffect(() => {
    onSendingChangeRef.current?.(isSending);
  }, [isSending]);

  // Stop reading a reply that is still streaming when the thread is unmounted
  useEffect(
    () => () => {
      sendAbortRef.current?.abort();
      onSendingChangeRef.current?.(false);
    },
    []
  );

  const turnCount = messages.filter((m) => m.role === 'user').length;
  const tokenTally = messages.reduce((sum, m) => sum + (m.tokens_used || 0), 0);
  const limitReached = turnCount >= maxTurns;

  const loadMessages = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const history = await fetchConversationMessages(auditId);
      setMessages(history.messages);
      setMaxTurns(history.max_turns);
    } catch (err: any) {
      setLoadError(err.message || 'Failed to load the follow-up discussion.');
    } finally {
      setIsLoading(false);
    }
  }, [auditId]);

  useEffect(() => {
    sendAbortRef.current?.abort();
    setMessages([]);
    setDraft('');
    setSendError(null);
    setConfirmClear(false);
    loadMessages();
  }, [loadMessages]);

  // Keep the latest content visible unless the operator has scrolled up to read earlier turns
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, pendingQuestion, streamText]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_TO_BOTTOM_THRESHOLD_PX;
  };

  const handleSend = async () => {
    const question = draft.trim();
    if (!question || isSending || limitReached) return;

    stickToBottomRef.current = true;
    setIsSending(true);
    setSendError(null);
    setConfirmClear(false);
    setPendingQuestion(question);
    setStreamText('');
    setStreamStatus(null);
    setDraft('');

    sendAbortRef.current?.abort();
    const controller = new AbortController();
    sendAbortRef.current = controller;
    const aborted = () => controller.signal.aborted;

    try {
      const res = await sendAiFollowUp(auditId, { message: question, provider, model, fallback_models: fallbackModels }, (evt) => {
        if (aborted()) return;
        if (evt.stage === 'delta' && evt.content) {
          setStreamText((prev) => prev + evt.content);
        } else if (evt.stage === 'calling') {
          // A fallback model streams its reply from the start
          setStreamText('');
          setStreamStatus(
            evt.is_fallback ? `Querying fallback model (${evt.model})...` : `Querying ${evt.model}...`
          );
        } else if (evt.stage === 'failover') {
          setStreamText('');
          setStreamStatus(`${evt.failed_model} failed. Failing over to ${evt.next_model}...`);
        }
      }, { signal: controller.signal });
      if (aborted()) return;

      try {
        const history = await fetchConversationMessages(auditId);
        if (aborted()) return;
        setMessages(history.messages);
        setMaxTurns(history.max_turns);
      } catch {
        // The exchange is saved; show it locally until the thread is next loaded
        setMessages((prev) => [
          ...prev,
          {
            id: -res.message.id,
            role: 'user',
            content: question,
            tokens_used: 0,
            model: res.model_used,
            created_at: res.message.created_at,
          },
          res.message,
        ]);
        setMaxTurns(res.max_turns);
      }
    } catch (err: any) {
      if (aborted()) return;
      setSendError(err.message || 'AI follow-up request failed.');
      setDraft(question);
    } finally {
      if (sendAbortRef.current === controller) sendAbortRef.current = null;
      setIsSending(false);
      setPendingQuestion(null);
      setStreamText('');
      setStreamStatus(null);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleClear = async () => {
    setIsClearing(true);
    setSendError(null);
    try {
      await clearConversationThread(auditId);
      setMessages([]);
      setConfirmClear(false);
    } catch (err: any) {
      setSendError(err.message || 'Failed to clear the follow-up discussion.');
    } finally {
      setIsClearing(false);
    }
  };

  if (isLoading) {
    return <LoadingSpinner message="Loading follow-up discussion..." className="p-4 text-center text-slate-500 font-mono text-xs flex items-center justify-center gap-2" />;
  }

  if (loadError) {
    return (
      <div className="p-3 bg-red-950/30 border border-red-900/60 rounded-lg flex items-start justify-between gap-2 text-xs text-red-300">
        <div className="flex items-start gap-2">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{loadError}</span>
        </div>
        <button
          type="button"
          onClick={loadMessages}
          className="inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] text-red-200 hover:bg-red-900/40 transition cursor-pointer shrink-0 min-h-[32px]"
        >
          <RefreshCw className="w-3 h-3" />
          Retry
        </button>
      </div>
    );
  }

  const hasThread = messages.length > 0 || pendingQuestion !== null;

  return (
    <div className="space-y-3 text-xs font-sans" data-testid="ai-follow-up-thread">
      {/* Turn counter, token tally and clear action */}
      <div className="flex flex-wrap items-center justify-between gap-2 font-mono text-[11px]">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`px-2 py-0.5 rounded border ${
              limitReached
                ? 'text-amber-300 bg-amber-950/60 border-amber-800/60'
                : 'text-slate-300 bg-dark-900 border-dark-700'
            }`}
          >
            {turnCount} / {maxTurns} turns
          </span>
          {tokenTally > 0 && (
            <span className="px-2 py-0.5 rounded border text-slate-300 bg-dark-900 border-dark-700">
              Tokens: <span className="text-slate-100 font-semibold">{tokenTally.toLocaleString()}</span>
            </span>
          )}
        </div>

        {messages.length > 0 && !isSending && (
          confirmClear ? (
            <div className="flex items-center gap-1.5">
              <span className="text-slate-400 font-sans">Clear this thread?</span>
              <button
                type="button"
                onClick={handleClear}
                disabled={isClearing}
                className="px-2.5 py-1 rounded bg-red-900/60 hover:bg-red-800/70 text-red-100 transition cursor-pointer disabled:opacity-50 min-h-[32px]"
              >
                {isClearing ? 'Clearing...' : 'Confirm'}
              </button>
              <button
                type="button"
                onClick={() => setConfirmClear(false)}
                disabled={isClearing}
                className="px-2.5 py-1 rounded text-slate-400 hover:text-slate-200 hover:bg-dark-800 transition cursor-pointer min-h-[32px]"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmClear(true)}
              className="inline-flex items-center gap-1.5 px-2 py-1 rounded text-slate-400 hover:text-slate-200 hover:bg-dark-800 transition cursor-pointer min-h-[32px]"
            >
              <Trash2 className="w-3.5 h-3.5" />
              Clear Thread
            </button>
          )
        )}
      </div>

      {/* Conversation */}
      {hasThread ? (
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="max-h-[28rem] overflow-y-auto space-y-3 pr-1"
        >
          {messages.map((m) =>
            m.role === 'user' ? (
              <div key={m.id} className="flex justify-end">
                <div className="max-w-[85%] px-3 py-2 rounded-lg bg-accent-950/40 border border-accent-800/60 text-slate-100 whitespace-pre-wrap break-words leading-relaxed">
                  {m.content}
                </div>
              </div>
            ) : (
              <div key={m.id} className="bg-dark-950 p-3 rounded-lg border border-dark-700 space-y-2">
                <MarkdownRenderer content={m.content} />
                <div className="flex flex-wrap items-center gap-2 pt-1.5 border-t border-dark-800 font-mono text-[10px] text-slate-400">
                  <span className="text-accent-400">{m.model}</span>
                  {m.tokens_used > 0 && <span>{m.tokens_used.toLocaleString()} tokens</span>}
                </div>
              </div>
            )
          )}

          {pendingQuestion !== null && (
            <>
              <div className="flex justify-end">
                <div className="max-w-[85%] px-3 py-2 rounded-lg bg-accent-950/40 border border-accent-800/60 text-slate-100 whitespace-pre-wrap break-words leading-relaxed">
                  {pendingQuestion}
                </div>
              </div>
              <div className="bg-dark-950 p-3 rounded-lg border border-dark-700 space-y-2" aria-live="polite">
                {streamText ? (
                  <MarkdownRenderer content={streamText} />
                ) : (
                  <div className="flex items-center gap-2 text-slate-400 font-mono text-[11px]">
                    <RefreshCw className="w-3.5 h-3.5 animate-spin text-accent-500" />
                    <span>{streamStatus || 'Waiting for reply...'}</span>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      ) : (
        <p className="text-slate-400 text-[11px] leading-relaxed">
          Ask about commands, flags, error codes or next steps for this diagnosis. Questions are redacted before they are sent to your AI provider.
        </p>
      )}

      {sendError && (
        <div className="p-2.5 bg-red-950/30 border border-red-900/60 rounded-lg flex items-start gap-2 text-red-300">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span className="break-words">{sendError}</span>
        </div>
      )}

      {/* Input or turn limit advisory */}
      {limitReached ? (
        <div className="p-3 bg-amber-950/30 border border-amber-800/50 rounded-lg flex items-start gap-2 text-amber-200/90 leading-relaxed">
          <Info className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
          <span>
            <span className="font-semibold text-amber-300">Turn limit reached.</span> This thread has used all {maxTurns} follow-up questions. Clear the thread to start a new discussion.
          </span>
        </div>
      ) : (
        <div className="space-y-1.5">
          <div className="flex items-end gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isSending}
              maxLength={MAX_QUESTION_LENGTH}
              rows={2}
              placeholder="Ask a follow-up question about this diagnosis..."
              aria-label="Follow-up question"
              className="flex-1 min-h-[44px] px-3 py-2 bg-dark-950 border border-dark-700 rounded-lg text-xs text-slate-200 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-sans resize-y disabled:opacity-60"
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={isSending || !draft.trim()}
              className="inline-flex items-center justify-center gap-1.5 min-h-[44px] min-w-[44px] px-3 rounded-lg bg-accent-600 hover:bg-accent-500 text-white font-medium transition cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed touch-manipulation"
              title="Send (Ctrl+Enter or Cmd+Enter)"
            >
              {isSending ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
              <span className="hidden sm:inline">Send</span>
            </button>
          </div>
          <div className="flex items-center justify-between font-mono text-[10px] text-slate-500">
            <span className="hidden sm:inline">Ctrl+Enter or Cmd+Enter to send</span>
            <span className="ml-auto">
              {draft.length} / {MAX_QUESTION_LENGTH}
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default AiFollowUpThread;
