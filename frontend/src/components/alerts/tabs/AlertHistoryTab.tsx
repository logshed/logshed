import React from 'react';
import {
  History,
  Trash2,
  CheckCircle2,
  Calendar,
  Brain,
  Zap,
  Bell,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { AlertHistoryItem } from '../../../types.ts';
import { extractCleanSummary } from '../../../utils/summary.ts';
import { formatLocalTimestamp } from '../../../utils/formatters.ts';

export interface AlertHistoryTabProps {
  historyItems: AlertHistoryItem[];
  historyTotal: number;
  isMobile: boolean;
  onClearAllHistory: () => void;
  onSelectHistoryItem: (item: AlertHistoryItem) => void;
  onDeleteHistoryItem: (item: AlertHistoryItem) => void;
  limit?: number;
  offset?: number;
  onPageChange?: (newOffset: number) => void;
}

export const AlertHistoryTab: React.FC<AlertHistoryTabProps> = ({
  historyItems,
  historyTotal,
  isMobile,
  onClearAllHistory,
  onSelectHistoryItem,
  onDeleteHistoryItem,
  limit = 50,
  offset = 0,
  onPageChange,
}) => {
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(historyTotal / limit));
  const startItem = historyTotal === 0 ? 0 : offset + 1;
  const endItem = Math.min(offset + historyItems.length, historyTotal);

  return (
    <div className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-xs">
      <div className="px-5 py-4 border-b border-dark-700 flex items-center justify-between">
        <div>
          <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
            <History className="w-4 h-4 text-accent-500" />
            <span>History</span>
          </h3>
          <p className="text-xs text-slate-400 mt-0.5">
            Previous alerts and on-demand analyses
          </p>
        </div>
        {historyItems.length > 0 && (
          <button
            type="button"
            onClick={onClearAllHistory}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs text-red-400 hover:text-red-300 bg-red-950/30 hover:bg-red-900/30 rounded border border-red-800/40 transition cursor-pointer"
          >
            <Trash2 className="w-3 h-3" />
            <span>Clear All</span>
          </button>
        )}
      </div>

      {historyItems.length === 0 ? (
        <div className="p-10 text-center space-y-2">
          <CheckCircle2 className="w-8 h-8 text-emerald-400 mx-auto" />
          <p className="text-xs text-slate-300 font-medium">No history recorded yet.</p>
          <p className="text-xs text-slate-500">
            When alert rules trigger or AI analyses run, records will appear here.
          </p>
        </div>
      ) : isMobile ? (
        /* Mobile Card View */
        <div className="divide-y divide-dark-800">
          {historyItems.map((item) => {
            const isDigest = item.rule_name === 'Daily Digest';
            const isOnDemand = !isDigest && !item.rule_id && (item.rule_name === 'On-Demand Analysis' || Boolean(item.ai_audit_id && !item.sample_log));
            const isAiAlert = !isOnDemand && !isDigest && Boolean(item.ai_enrichment);
            const displayTarget = isDigest
              ? 'Daily Digest'
              : isOnDemand
              ? (item.source_alias && item.app_name ? `${item.source_alias} • ${item.app_name}` : item.source_alias || item.app_name || 'On-Demand')
              : item.rule_name;
            const summaryText = extractCleanSummary(item.incident_summary || item.sample_log || '');

            return (
              <div
                key={item.id}
                onClick={() => onSelectHistoryItem(item)}
                className="p-3.5 space-y-2 hover:bg-dark-800/40 transition cursor-pointer select-none"
              >
                {/* Line 1: Type Badge + Target & Timestamp */}
                <div className="flex items-center justify-between text-xs gap-2">
                  <div className="flex items-center gap-1.5 truncate">
                    {isDigest ? (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-purple-400 bg-purple-950/60 border border-purple-800/60 shrink-0">
                        <Calendar className="w-2.5 h-2.5 shrink-0" />
                        Daily Digest
                      </span>
                    ) : isOnDemand ? (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-accent-400 bg-accent-950/60 border border-accent-800/60 shrink-0">
                        <Brain className="w-2.5 h-2.5 shrink-0" />
                        On-Demand
                      </span>
                    ) : isAiAlert ? (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-amber-400 bg-amber-950/60 border border-amber-800/60 shrink-0">
                        <Zap className="w-2.5 h-2.5 shrink-0" />
                        AI Alert
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-slate-300 bg-dark-800 border border-dark-650 shrink-0">
                        <Bell className="w-2.5 h-2.5 text-slate-400 shrink-0" />
                        Alert
                      </span>
                    )}
                    <span className="truncate font-sans font-semibold text-slate-100">
                      {displayTarget}
                    </span>
                  </div>
                  <span className="text-[10px] text-slate-500 font-mono shrink-0">
                    {formatLocalTimestamp(item.triggered_at)}
                  </span>
                </div>

                {/* Line 2: Clean Summary */}
                <div
                  className="text-slate-300 text-xs line-clamp-2 leading-relaxed font-sans"
                  title={summaryText}
                >
                  {summaryText || '-'}
                </div>

                {/* Line 3: Model / Count + Actions */}
                <div className="flex items-center justify-between pt-1 text-[11px] text-slate-400">
                  <div className="flex items-center gap-2">
                    {item.ai_model && (
                      <span className="bg-dark-950 border border-dark-700 px-1.5 py-0.5 rounded text-[10px] text-slate-300 font-mono truncate max-w-[140px]">
                        {item.ai_model}
                      </span>
                    )}
                    <span className="bg-dark-950 border border-dark-700 px-1.5 py-0.5 rounded text-[10px] text-slate-300 font-mono">
                      {item.trigger_count} {isDigest ? 'logs' : isOnDemand ? `log${item.trigger_count === 1 ? '' : 's'}` : `event${item.trigger_count === 1 ? '' : 's'}`}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 font-sans shrink-0" onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      onClick={() => onSelectHistoryItem(item)}
                      className="px-2 py-0.5 text-[11px] font-mono text-accent-400 bg-accent-950/50 hover:bg-accent-900/60 border border-accent-800/80 rounded transition cursor-pointer"
                    >
                      View
                    </button>
                    <button
                      type="button"
                      onClick={() => onDeleteHistoryItem(item)}
                      className="p-1 text-slate-400 hover:text-red-400 hover:bg-red-950/50 rounded transition cursor-pointer"
                      title="Delete history record"
                      aria-label={`Delete record ${item.id}`}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        /* Desktop Table View */
        <div className="divide-y divide-dark-800 font-mono text-xs">
          <div className="grid grid-cols-[140px_110px_200px_1fr_95px] px-4 py-2 text-slate-400 font-medium text-xs font-sans bg-dark-950/60 border-b border-dark-700 select-none">
            <div>Time</div>
            <div>Type</div>
            <div>Target / Rule</div>
            <div>Summary</div>
            <div className="text-right">Actions</div>
          </div>

          {historyItems.map((item) => {
            const isDigest = item.rule_name === 'Daily Digest';
            const isOnDemand = !isDigest && !item.rule_id && (item.rule_name === 'On-Demand Analysis' || Boolean(item.ai_audit_id && !item.sample_log));
            const isAiAlert = !isOnDemand && !isDigest && Boolean(item.ai_enrichment);
            const displayTarget = isDigest
              ? 'Daily Digest'
              : isOnDemand
              ? (item.source_alias && item.app_name ? `${item.source_alias} • ${item.app_name}` : item.source_alias || item.app_name || 'On-Demand')
              : item.rule_name;
            const summaryText = extractCleanSummary(item.incident_summary || item.sample_log || '');

            return (
              <div
                key={item.id}
                onClick={() => onSelectHistoryItem(item)}
                className="grid grid-cols-[140px_110px_200px_1fr_95px] px-4 py-2.5 items-center hover:bg-dark-800 transition text-[11px] cursor-pointer group select-none"
              >
                <div className="text-slate-400 group-hover:text-slate-300 font-mono">
                  {formatLocalTimestamp(item.triggered_at)}
                </div>
                <div>
                  {isDigest ? (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-purple-400 bg-purple-950/60 border border-purple-800/60">
                      <Calendar className="w-2.5 h-2.5 shrink-0" />
                      Daily Digest
                    </span>
                  ) : isOnDemand ? (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-accent-400 bg-accent-950/60 border border-accent-800/60">
                      <Brain className="w-2.5 h-2.5 shrink-0" />
                      On-Demand
                    </span>
                  ) : isAiAlert ? (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-amber-400 bg-amber-950/60 border border-amber-800/60">
                      <Zap className="w-2.5 h-2.5 shrink-0" />
                      AI Alert
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium text-slate-300 bg-dark-800 border border-dark-650">
                      <Bell className="w-2.5 h-2.5 text-slate-400 shrink-0" />
                      Alert
                    </span>
                  )}
                </div>
                <div className="truncate pr-2 font-sans font-medium text-slate-200" title={displayTarget}>
                  {displayTarget}
                </div>
                <div
                  className="text-slate-300 truncate pr-2 group-hover:text-white font-sans text-xs"
                  title={summaryText}
                >
                  {summaryText || '-'}
                </div>
                <div className="flex items-center justify-end gap-1.5 font-sans" onClick={(e) => e.stopPropagation()}>
                  <button
                    type="button"
                    onClick={() => onSelectHistoryItem(item)}
                    className="px-2 py-0.5 text-[11px] font-mono text-accent-400 bg-accent-950/50 hover:bg-accent-900/60 border border-accent-800/80 rounded transition cursor-pointer"
                    title="View details"
                  >
                    View
                  </button>
                  <button
                    type="button"
                    onClick={() => onDeleteHistoryItem(item)}
                    className="p-1 text-slate-400 hover:text-red-400 hover:bg-red-950/50 rounded border border-transparent hover:border-red-900/50 transition cursor-pointer"
                    title="Delete history record"
                    aria-label={`Delete record ${item.id}`}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Pagination Footer */}
      {historyTotal > limit && onPageChange && (
        <div className="px-5 py-3 border-t border-dark-800 flex items-center justify-between text-xs text-slate-400 font-mono">
          <div>
            Showing {startItem} to {endItem} of {historyTotal} records
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={offset <= 0}
              onClick={() => onPageChange(Math.max(0, offset - limit))}
              className="p-1.5 rounded bg-dark-800 hover:bg-dark-750 text-slate-300 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
              title="Previous page"
              aria-label="Previous page"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span>
              Page {currentPage} of {totalPages}
            </span>
            <button
              type="button"
              disabled={offset + limit >= historyTotal}
              onClick={() => onPageChange(offset + limit)}
              className="p-1.5 rounded bg-dark-800 hover:bg-dark-750 text-slate-300 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
              title="Next page"
              aria-label="Next page"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default AlertHistoryTab;
