import React, { useState, useMemo } from 'react';
import {
  Sparkles,
  Brain,
  Bell,
  Zap,
  Copy,
  Check,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Info,
  Calendar,
} from 'lucide-react';
import { AlertHistoryItem } from '../../types.ts';
import { MarkdownRenderer } from '../common/MarkdownRenderer.tsx';
import { useClipboard } from '../../utils/hooks.ts';
import { IncidentStatusBadge } from './IncidentStatusBadge.tsx';
import { DEFAULT_SYSTEM_PROMPT, buildFullEnvelope } from '../../utils/aiPrompt.ts';

interface IncidentHistoryDetailProps {
  item: AlertHistoryItem;
  channelName?: string;
}

export const IncidentHistoryDetail: React.FC<IncidentHistoryDetailProps> = ({
  item,
  channelName,
}) => {
  const { copied: copiedLog, copy: copyLog } = useClipboard();
  const { copied: copiedPrompt, copy: copyPrompt } = useClipboard();
  const [showPromptDetails, setShowPromptDetails] = useState<boolean>(false);
  const [promptViewMode, setPromptViewMode] = useState<'analysis' | 'full'>('analysis');

  const isDigest = item.rule_name === 'Daily Digest';
  const isOnDemand = !isDigest && !item.rule_id && (item.rule_name === 'On-Demand Analysis' || Boolean(item.ai_audit_id && !item.sample_log));
  const isAiAlert = !isOnDemand && !isDigest && Boolean(item.ai_enrichment);
  const isFailed = Boolean(
    item.ai_enrichment &&
      (!item.incident_summary ||
        item.incident_summary.startsWith('AI analysis failed:') ||
        item.incident_summary.startsWith('AI enrichment failed:'))
  );

  const displayTarget = isDigest
    ? 'Daily Digest'
    : isOnDemand
    ? (item.source_alias && item.app_name ? `${item.source_alias} • ${item.app_name}` : item.source_alias || item.app_name || 'On-Demand Analysis')
    : item.rule_name;

  const countLabel = isDigest
    ? `${item.trigger_count.toLocaleString()} logs analyzed`
    : isOnDemand
    ? `${item.trigger_count} log${item.trigger_count === 1 ? '' : 's'}`
    : `${item.trigger_count} matching event${item.trigger_count === 1 ? '' : 's'}`;

  const cleanUserContext = item.user_context && !item.user_context.toLowerCase().startsWith('alert rule:')
    ? item.user_context
    : null;

  const handleCopyPrompt = async () => {
    if (item.prompt_sent) {
      const textToCopy =
        promptViewMode === 'full'
          ? buildFullEnvelope(item.system_prompt || DEFAULT_SYSTEM_PROMPT, item.prompt_sent)
          : item.prompt_sent;
      await copyPrompt(textToCopy);
    }
  };

  const displaySummary = useMemo(() => {
    if (!item.incident_summary) return '';
    if (isDigest) {
      return item.incident_summary
        .replace(/\n*\[Link to LogShed[^\]]*\]\([^)]+\)/gi, '')
        .trim();
    }
    return item.incident_summary;
  }, [item.incident_summary, isDigest]);

  return (
    <div className="space-y-4 font-sans text-xs">
      {/* Header Info Card */}
      <div className="bg-dark-950 p-4 rounded-lg border border-dark-700 space-y-3">
        {/* Row 1: Target / Title, Type Badge & Timestamp */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            {isDigest ? (
              <Calendar className="w-4 h-4 text-purple-400 shrink-0" />
            ) : isOnDemand ? (
              <Brain className="w-4 h-4 text-accent-400 shrink-0" />
            ) : isAiAlert ? (
              <Zap className="w-4 h-4 text-amber-400 shrink-0" />
            ) : (
              <Bell className="w-4 h-4 text-slate-400 shrink-0" />
            )}
            <span className="font-semibold text-slate-100 font-mono text-xs truncate">
              {displayTarget}
            </span>
            <span className="text-slate-400 text-[11px] shrink-0 font-mono">
              ({countLabel})
            </span>
            {isDigest ? (
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-sans font-medium text-purple-400 bg-purple-950/60 border border-purple-800/60 shrink-0">
                <Calendar className="w-2.5 h-2.5 text-purple-400 shrink-0" />
                Daily Digest
              </span>
            ) : isOnDemand ? (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-sans font-medium text-accent-400 bg-accent-950/60 border border-accent-800/60 shrink-0">
                On-Demand
              </span>
            ) : isAiAlert ? (
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-sans font-medium text-amber-400 bg-amber-950/60 border border-amber-800/60 shrink-0">
                <Zap className="w-2.5 h-2.5 text-amber-400 shrink-0" />
                AI Alert
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-sans font-medium text-slate-300 bg-dark-800 border border-dark-650 shrink-0">
                Alert
              </span>
            )}
          </div>
          <span className="font-mono text-[11px] text-slate-400 shrink-0">
            {new Date(item.triggered_at).toLocaleString()}
          </span>
        </div>

        {/* Row 2: Secondary Context / Channel / Status */}
        <div className="flex flex-wrap items-center justify-between pt-2.5 border-t border-dark-800 text-[11px] font-mono gap-2">
          <div className="flex flex-wrap items-center gap-2">
            {item.ai_model && (
              <div className="flex items-center gap-1.5 bg-dark-900 border border-dark-700 px-2 py-0.5 rounded text-slate-300">
                <span className="text-slate-400 text-[10px] uppercase font-semibold">Model:</span>
                <span className="text-accent-400 font-medium">{item.ai_model}</span>
              </div>
            )}
            {channelName && (
              <div className="flex items-center gap-1.5 bg-dark-900 border border-dark-700 px-2 py-0.5 rounded text-slate-300">
                <span className="text-slate-400 text-[10px] uppercase font-semibold">Target:</span>
                <span className="text-slate-200 font-medium">{channelName}</span>
              </div>
            )}
            {!isOnDemand && !isDigest && (item.source_alias || item.app_name) && (
              <div className="flex items-center gap-1.5 bg-dark-900 border border-dark-700 px-2 py-0.5 rounded text-slate-300">
                <span className="text-slate-400 text-[10px] uppercase font-semibold">Source:</span>
                <span className="text-slate-200 font-medium">
                  {[item.source_alias, item.app_name].filter(Boolean).join(' • ')}
                </span>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            <IncidentStatusBadge
              aiEnrichment={item.ai_enrichment}
              incidentSummary={item.incident_summary}
            />
          </div>
        </div>

        {/* Row 3: Token Breakdown Pills (when available) */}
        {item.tokens_used !== undefined && item.tokens_used !== null && item.tokens_used > 0 && (
          <div className="flex flex-wrap items-center justify-between pt-2 border-t border-dark-800 text-[11px] font-mono gap-2">
            <span className="text-slate-400 text-[10px] uppercase font-semibold">Tokens:</span>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-slate-300">
                Total: <span className="text-slate-100 font-semibold">{item.tokens_used.toLocaleString()}</span>
              </span>
              {item.tokens_in !== undefined && item.tokens_in !== null && (
                <span className="bg-emerald-950/60 border border-emerald-800/80 text-emerald-300 px-1.5 py-0.5 rounded">
                  ↓ {item.tokens_in.toLocaleString()} in
                </span>
              )}
              {item.tokens_out !== undefined && item.tokens_out !== null && (
                <span className="bg-sky-950/60 border border-sky-800/80 text-sky-300 px-1.5 py-0.5 rounded">
                  ↑ {item.tokens_out.toLocaleString()} out
                </span>
              )}
              {Boolean(item.tokens_thoughts) && (
                <span className="bg-purple-950/60 border border-purple-800/80 text-purple-300 px-1.5 py-0.5 rounded">
                  ⚡ {item.tokens_thoughts!.toLocaleString()} thinking
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Operator Context Banner */}
      {cleanUserContext && (
        <div className="bg-dark-900 border border-dark-700 p-3 rounded-lg text-xs space-y-1">
          <div className="font-semibold text-slate-300 uppercase tracking-wider text-[10px]">
            Operator Context:
          </div>
          <div className="text-slate-200 leading-relaxed font-sans">
            {cleanUserContext}
          </div>
        </div>
      )}

      {/* Advisory Notice Banner for AI Analyses */}
      {item.ai_enrichment && (
        <div className="p-3 bg-amber-950/30 border border-amber-800/50 rounded-lg flex items-start gap-2 text-amber-200/90 text-xs leading-relaxed">
          <Info className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
          <span>
            AI root-cause analyses and remediation commands are advisory only. Always verify proposed commands and configurations before executing on systems. API calls consume tokens billed to your provider.
          </span>
        </div>
      )}

      {/* Rendered Full Diagnosis / Incident Summary Card */}
      {item.incident_summary && (
        <div className="bg-dark-950 p-4 rounded-lg border border-dark-700">
          <h4
            className={`text-[11px] font-semibold uppercase tracking-wider mb-2 flex items-center gap-1.5 ${
              isFailed ? 'text-red-400' : 'text-accent-400'
            }`}
          >
            {isFailed ? (
              <>
                <AlertCircle className="w-3.5 h-3.5 text-red-400" />
                Failure Details
              </>
            ) : isDigest ? (
              <>
                <Calendar className="w-3.5 h-3.5 text-purple-400" />
                Daily Digest Rollup Report
              </>
            ) : (
              <>
                <Sparkles className="w-3.5 h-3.5 text-accent-400" />
                AI Incident Diagnosis & Remediation
              </>
            )}
          </h4>

          {isFailed ? (
            <div className="p-3 bg-red-950/30 border border-red-900/60 rounded text-xs text-red-300 leading-relaxed font-mono whitespace-pre-wrap">
              {item.incident_summary}
            </div>
          ) : (
            <MarkdownRenderer content={displaySummary} />
          )}
        </div>
      )}

      {/* Expandable Prompt & Redacted Logs Card */}
      {item.prompt_sent && (
        <div className="border border-dark-700 rounded-lg overflow-hidden bg-dark-950">
          <button
            type="button"
            onClick={() => setShowPromptDetails(!showPromptDetails)}
            className="w-full flex items-center justify-between p-3 bg-dark-900 hover:bg-dark-850 transition text-left cursor-pointer"
          >
            <div className="flex items-center gap-2">
              {showPromptDetails ? (
                <ChevronDown className="w-4 h-4 text-slate-400" />
              ) : (
                <ChevronRight className="w-4 h-4 text-slate-400" />
              )}
              <span className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider">
                View Submitted Logs & Prompt
              </span>
            </div>
            <span className="text-[11px] text-slate-400 font-mono">
              {showPromptDetails ? 'Hide' : 'Expand'}
            </span>
          </button>

          {showPromptDetails && (
            <div className="border-t border-dark-700 bg-dark-950">
              <div className="p-2.5 px-3 bg-dark-900/60 border-b border-dark-800 flex items-center justify-between gap-2">
                <div className="inline-flex rounded-md p-0.5 bg-dark-950 border border-dark-700 text-[11px]">
                  <button
                    type="button"
                    onClick={() => setPromptViewMode('analysis')}
                    className={`px-2 py-0.5 rounded transition cursor-pointer ${
                      promptViewMode === 'analysis'
                        ? 'bg-accent-600 text-white font-medium'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Analysis Prompt
                  </button>
                  <button
                    type="button"
                    onClick={() => setPromptViewMode('full')}
                    className={`px-2 py-0.5 rounded transition cursor-pointer ${
                      promptViewMode === 'full'
                        ? 'bg-accent-600 text-white font-medium'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Full LLM Prompt
                  </button>
                </div>

                <button
                  type="button"
                  onClick={handleCopyPrompt}
                  className="inline-flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-medium text-slate-400 hover:text-slate-200 hover:bg-dark-800 transition cursor-pointer shrink-0 min-h-[32px] touch-manipulation select-none active:bg-dark-750"
                  title="Copy prompt"
                >
                  {copiedPrompt ? (
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                  ) : (
                    <Copy className="w-3.5 h-3.5" />
                  )}
                  <span>{copiedPrompt ? 'Copied' : 'Copy Prompt'}</span>
                </button>
              </div>

              <pre className="p-3 bg-dark-950 font-mono text-[11px] text-slate-300 overflow-x-auto whitespace-pre-wrap max-h-60 leading-relaxed selection:bg-accent-900/50">
                {promptViewMode === 'full'
                  ? buildFullEnvelope(item.system_prompt || DEFAULT_SYSTEM_PROMPT, item.prompt_sent)
                  : item.prompt_sent}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* Triggering Log Snippet Card */}
      {!isDigest && item.sample_log && (
        <div className="border border-dark-700 rounded-lg overflow-hidden bg-dark-950">
          <div className="flex items-center justify-between p-2.5 px-3 bg-dark-900 border-b border-dark-700 gap-2">
            <h4 className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider">
              Triggering Log Snippet
            </h4>
            <button
              type="button"
              onClick={() => copyLog(item.sample_log || '')}
              className="inline-flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-medium text-slate-400 hover:text-slate-200 hover:bg-dark-800 transition cursor-pointer shrink-0 min-h-[32px] touch-manipulation select-none active:bg-dark-750"
              title="Copy triggering log"
            >
              {copiedLog ? (
                <Check className="w-3.5 h-3.5 text-emerald-400" />
              ) : (
                <Copy className="w-3.5 h-3.5" />
              )}
              <span>{copiedLog ? 'Copied' : 'Copy Log'}</span>
            </button>
          </div>
          <pre className="p-3 bg-dark-950 font-mono text-[11px] text-slate-300 overflow-x-auto whitespace-pre-wrap max-h-48 leading-relaxed selection:bg-accent-900/50">
            {item.sample_log}
          </pre>
        </div>
      )}
    </div>
  );
};
