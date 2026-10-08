import React, { useEffect, useMemo, useState } from 'react';
import { Sparkles, Copy, Check, Plus, Edit2, Layers, Terminal, FilterX, Trash2, AlertTriangle, RefreshCw, AlertCircle, ChevronLeft, ChevronRight } from 'lucide-react';
import { LogEntry } from '../../types.ts';
import { fetchLogContext, deleteSingleLog } from '../../api/logs.ts';
import { SeverityBadge } from '../common/SeverityBadge.tsx';
import { SlideOver } from '../common/SlideOver.tsx';
import { stripAnsi, cleanLogMessageForDisplay, formatLocalTimestamp, formatFullTimestamp } from '../../utils/formatters.ts';
import { getCachedServerTimezone, getCachedServerTzName } from '../../api/settings.ts';
import { useClipboard } from '../../utils/hooks.ts';

interface LogDetailModalProps {
  log: LogEntry | null;
  isOpen: boolean;
  onClose: () => void;
  onExplainWithAi: (log: LogEntry, contextLogs?: LogEntry[]) => void;
  onInspectWithContext?: (logs: LogEntry[]) => void;
  onAddAlias?: (ip: string) => void;
  isHostAliased?: boolean;
  onCreateDropRule?: (log: LogEntry) => void;
  onDeleteLog?: (log: LogEntry) => void;
  onNavigatePrevious?: () => void;
  onNavigateNext?: () => void;
  hasPreviousLog?: boolean;
  hasNextLog?: boolean;
  timeZone?: string;
  tzName?: string;
}

export const LogDetailModal: React.FC<LogDetailModalProps> = ({
  log,
  isOpen,
  onClose,
  onExplainWithAi,
  onInspectWithContext,
  onAddAlias,
  isHostAliased,
  onCreateDropRule,
  onDeleteLog,
  onNavigatePrevious,
  onNavigateNext,
  hasPreviousLog,
  hasNextLog,
  timeZone,
  tzName,
}) => {
  const effectiveTimezone = timeZone || getCachedServerTimezone();
  const effectiveTzName = tzName || getCachedServerTzName();
  const hostIsAliased = Boolean(
    isHostAliased ?? (log && log.source_alias && log.source_alias !== log.source_ip)
  );
  const { copied: copiedRaw, copy: copyRaw } = useClipboard();
  const { copied: copiedMsg, copy: copyMsg } = useClipboard();
  const [contextLogs, setContextLogs] = useState<LogEntry[]>([]);
  const [isLoadingContext, setIsLoadingContext] = useState(false);
  const [showContext, setShowContext] = useState(false);
  const [sameAppOnly, setSameAppOnly] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) {
      setShowDeleteConfirm(false);
      setIsDeleting(false);
      setDeleteError(null);
    }
  }, [isOpen]);

  const handleConfirmDelete = async () => {
    if (!log) return;
    try {
      setIsDeleting(true);
      setDeleteError(null);
      await deleteSingleLog(log.id);
      if (onDeleteLog) {
        onDeleteLog(log);
      }
      onClose();
    } catch (err: any) {
      setDeleteError(err.message || 'Failed to delete log entry.');
    } finally {
      setIsDeleting(false);
    }
  };

  useEffect(() => {
    if (isOpen && log && showContext) {
      loadContext(log.id, sameAppOnly);
    }
  }, [isOpen, log, showContext, sameAppOnly]);

  const loadContext = async (logId: number, sameApp: boolean = false) => {
    try {
      setIsLoadingContext(true);
      const res = await fetchLogContext(logId, 10, sameApp);
      setContextLogs(res.logs);
    } catch (err) {
      console.error('Failed to load log context', err);
    } finally {
      setIsLoadingContext(false);
    }
  };

  // Transfer target log plus loaded surrounding context logs strictly respecting single-host constraint
  const targetAndContextLogs = useMemo(() => {
    if (!log) return [];
    const logMap = new Map<number, LogEntry>();
    logMap.set(log.id, log);
    for (const ctxLog of contextLogs) {
      if (ctxLog.source_alias === log.source_alias) {
        logMap.set(ctxLog.id, ctxLog);
      }
    }
    return Array.from(logMap.values()).sort((a, b) => {
      const cmp = a.timestamp.localeCompare(b.timestamp);
      return cmp !== 0 ? cmp : a.id - b.id;
    });
  }, [log, contextLogs]);

  const handleInspectTargetAndContext = () => {
    if (!log || targetAndContextLogs.length === 0) return;
    if (onInspectWithContext) {
      onInspectWithContext(targetAndContextLogs);
    } else {
      onExplainWithAi(log, targetAndContextLogs);
    }
    onClose();
  };

  if (!log) return null;

  const isDockerSource =
    log.source_ip === 'docker' || log.source_alias?.toLowerCase() === 'docker';
  const cleanedPayload = cleanLogMessageForDisplay(log.message);

  const handleCopyRaw = async () => {
    await copyRaw(log.raw);
  };

  const handleCopyMsg = async () => {
    await copyMsg(cleanedPayload);
  };

  return (
    <SlideOver
      isOpen={isOpen}
      onClose={onClose}
      title={`Log Record #${log.id} - ${log.source_alias}`}
      subtitle={
        (onNavigatePrevious || onNavigateNext) ? (
          <div className="flex items-center gap-1.5 pt-0.5">
            <button
              type="button"
              onClick={onNavigatePrevious}
              disabled={!hasPreviousLog}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-dark-800 hover:bg-dark-750 text-slate-200 border border-dark-700 hover:border-dark-600 transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed select-none"
              aria-label="Previous log"
              title="Previous log in current stream view"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
              <span>Previous</span>
            </button>
            <button
              type="button"
              onClick={onNavigateNext}
              disabled={!hasNextLog}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-dark-800 hover:bg-dark-750 text-slate-200 border border-dark-700 hover:border-dark-600 transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed select-none"
              aria-label="Next log"
              title="Next log in current stream view"
            >
              <span>Next</span>
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : undefined
      }
      width="max-w-3xl"
    >
      <div className="space-y-4 text-xs font-sans">
        {/* Action Header */}
        <div className="flex flex-wrap items-center justify-between gap-2 p-3 bg-dark-950 rounded-lg border border-dark-700">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={log.severity} />
            <span className="font-mono text-slate-300 font-medium">{log.app_name}</span>
            <span className="text-slate-500">•</span>
            <span className="font-mono text-slate-400">{log.source_alias}</span>
            <span className="text-slate-500">({log.source_ip})</span>
          </div>

          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 w-full sm:w-auto">
            {onAddAlias && (
              hostIsAliased ? (
                <button
                  onClick={() => {
                    onAddAlias(log.source_ip);
                    onClose();
                  }}
                  className="flex items-center justify-center gap-1 px-2.5 py-1 bg-dark-800 hover:bg-dark-700 text-slate-200 border border-dark-600 rounded transition font-medium"
                >
                  <Edit2 className="w-3.5 h-3.5 text-accent-400" />
                  <span>Modify Host Alias</span>
                </button>
              ) : (
                <button
                  onClick={() => {
                    onAddAlias(log.source_ip);
                    onClose();
                  }}
                  className="flex items-center justify-center gap-1 px-2.5 py-1 bg-dark-800 hover:bg-dark-700 text-slate-200 border border-dark-600 rounded transition font-medium"
                >
                  <Plus className="w-3.5 h-3.5 text-accent-400" />
                  <span>Add Host Alias</span>
                </button>
              )
            )}

            {onCreateDropRule && (
              <button
                onClick={() => onCreateDropRule(log)}
                className="flex items-center justify-center gap-1.5 px-2.5 py-1 bg-dark-800 hover:bg-dark-700 text-slate-200 border border-dark-600 rounded transition font-medium cursor-pointer"
                title="Create an ingestion drop rule for similar logs"
              >
                <FilterX className="w-3.5 h-3.5 text-accent-400" />
                <span>Create Drop Rule</span>
              </button>
            )}

            {onDeleteLog && (
              <button
                onClick={() => setShowDeleteConfirm(!showDeleteConfirm)}
                className="flex items-center justify-center gap-1.5 px-2.5 py-1 bg-dark-800 hover:bg-red-950 text-slate-300 hover:text-red-300 border border-dark-600 hover:border-red-800 rounded transition font-medium cursor-pointer"
                title="Delete this log record permanently"
              >
                <Trash2 className="w-3.5 h-3.5 text-red-400" />
                <span>Delete</span>
              </button>
            )}

            <button
              onClick={() => onExplainWithAi(log)}
              className="flex items-center justify-center gap-1.5 px-3 py-1 bg-accent-600 hover:bg-accent-500 text-white rounded font-medium transition shadow-xs"
            >
              <Sparkles className="w-3.5 h-3.5" />
              <span>Explain with AI</span>
            </button>
          </div>
        </div>

        {/* Delete Confirmation Banner */}
        {showDeleteConfirm && (
          <div className="p-3 bg-red-950/70 border border-red-800 rounded-lg flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-xs text-red-200 animate-in fade-in">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
              <span>Permanently delete log record #{log.id}? This action cannot be reversed.</span>
            </div>
            <div className="flex items-center gap-2 self-end sm:self-auto">
              <button
                type="button"
                onClick={() => setShowDeleteConfirm(false)}
                disabled={isDeleting}
                className="px-2.5 py-1 text-xs text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDelete}
                disabled={isDeleting}
                className="px-3 py-1 text-xs bg-red-600 hover:bg-red-500 text-white font-medium rounded transition flex items-center gap-1 cursor-pointer"
              >
                {isDeleting ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                <span>Confirm Delete</span>
              </button>
            </div>
          </div>
        )}

        {deleteError && (
          <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <span>{deleteError}</span>
          </div>
        )}


        {/* Structured Metadata Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-2">
          <div className="bg-dark-950 p-2.5 rounded border border-dark-700">
            <span className="text-[10px] uppercase font-semibold text-slate-400 block mb-1">
              Timestamp {effectiveTzName ? `(${effectiveTzName})` : ''}
            </span>
            <span className="font-mono text-slate-200 text-xs break-all select-all block">
              {formatFullTimestamp(log.timestamp, effectiveTimezone)}
            </span>
            <span className="text-[10px] font-mono text-slate-500 break-all select-all block mt-0.5" title="Universal Coordinated Time stored in database">
              UTC: {log.timestamp}
            </span>
          </div>
          <div className="bg-dark-950 p-2.5 rounded border border-dark-700">
            <span className="text-[10px] uppercase font-semibold text-slate-400 block mb-1">
              Received At {effectiveTzName ? `(${effectiveTzName})` : ''}
            </span>
            <span className="font-mono text-slate-200 text-xs break-all select-all block">
              {formatFullTimestamp(log.received_at, effectiveTimezone)}
            </span>
            <span className="text-[10px] font-mono text-slate-500 break-all select-all block mt-0.5" title="Universal Coordinated Time stored in database">
              UTC: {log.received_at}
            </span>
          </div>
          <div className="bg-dark-950 p-2.5 rounded border border-dark-700">
            <span className="text-[10px] uppercase font-semibold text-slate-400 block mb-1">Facility</span>
            <span className="font-mono text-slate-200 text-xs break-all">{log.facility}</span>
          </div>
          <div className="bg-dark-950 p-2.5 rounded border border-dark-700">
            <span className="text-[10px] uppercase font-semibold text-slate-400 block mb-1">Severity Code</span>
            <span className="font-mono text-slate-200 text-xs break-all">{log.severity}</span>
          </div>
        </div>

        {/* Formatted Message Box */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-1">
              <Terminal className="w-3.5 h-3.5 text-slate-400" />
              <span>Message Payload</span>
            </span>
            <button
              type="button"
              onClick={handleCopyMsg}
              className="inline-flex items-center gap-1.5 px-2 py-1 -mr-1 rounded text-[11px] font-medium text-slate-400 hover:text-slate-200 hover:bg-dark-800 transition cursor-pointer min-h-[32px] touch-manipulation select-none active:bg-dark-750"
              title="Copy message payload"
            >
              {copiedMsg ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copiedMsg ? 'Copied' : 'Copy Message'}</span>
            </button>
          </div>
          <div className="bg-dark-950 border border-dark-700 rounded-lg p-3 font-mono text-slate-200 text-xs whitespace-pre-wrap break-all select-text max-h-60 overflow-y-auto">
            {/* Sanitized text element without dangerouslySetInnerHTML */}
            {cleanedPayload}
          </div>
        </div>

        {/* Raw Syslog / Container Envelope */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11px] font-semibold text-slate-300 uppercase tracking-wider">
              {isDockerSource ? 'Raw Container Output' : 'Raw Syslog Packet'}
            </span>
            <button
              type="button"
              onClick={handleCopyRaw}
              className="inline-flex items-center gap-1.5 px-2 py-1 -mr-1 rounded text-[11px] font-medium text-slate-400 hover:text-slate-200 hover:bg-dark-800 transition cursor-pointer min-h-[32px] touch-manipulation select-none active:bg-dark-750"
              title="Copy raw log record"
            >
              {copiedRaw ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copiedRaw ? 'Copied' : 'Copy Raw'}</span>
            </button>
          </div>
          <div className="bg-dark-950 border border-dark-700 rounded-lg p-3 font-mono text-slate-400 text-[11px] whitespace-pre-wrap break-all select-text max-h-36 overflow-y-auto">
            {stripAnsi(log.raw)}
          </div>
        </div>

        {/* Surrounding Context Inspector */}
        <div className="pt-2 border-t border-dark-800">
          <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
            <button
              onClick={() => setShowContext(!showContext)}
              className="flex items-center gap-1.5 font-medium text-slate-300 hover:text-accent-400 transition"
            >
              <Layers className="w-3.5 h-3.5" />
              <span>{showContext ? 'Hide Surrounding Context' : 'Load Surrounding Context (±5 Lines)'}</span>
            </button>

            {showContext && (
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-1.5 text-[11px] text-slate-400 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={sameAppOnly}
                    onChange={(e) => setSameAppOnly(e.target.checked)}
                    className="rounded bg-dark-900 border-dark-700 text-accent-600 focus:ring-0 focus:ring-offset-0 w-3 h-3"
                  />
                  <span>Same App Only ({log.app_name})</span>
                </label>

                {contextLogs.length > 0 && !isLoadingContext && (
                  <button
                    onClick={handleInspectTargetAndContext}
                    title="Add Context to AI Analysis"
                    className="flex items-center gap-1.5 px-2.5 py-1 bg-accent-600 hover:bg-accent-500 text-white rounded font-medium transition text-[11px] shadow-xs"
                  >
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>Inspect Target + Context ({targetAndContextLogs.length} logs)</span>
                  </button>
                )}
              </div>
            )}
          </div>

          {showContext && (
            <div className="bg-dark-950 border border-dark-700 rounded-lg overflow-hidden">
              {isLoadingContext ? (
                <div className="p-4 text-center text-slate-400 font-mono">Loading surrounding context...</div>
              ) : contextLogs.length === 0 ? (
                <div className="p-4 text-center text-slate-500 font-mono">No surrounding logs found.</div>
              ) : (
                <div className="font-mono text-[11px] divide-y divide-dark-800 max-h-80 overflow-y-auto">
                  {contextLogs.map((ctxLog) => {
                    const isTarget = ctxLog.id === log.id;
                    return (
                      <div
                        key={ctxLog.id}
                        className={`p-2 flex items-start gap-2 ${
                          isTarget ? 'bg-accent-950/40 border-l-2 border-accent-500' : 'hover:bg-dark-900/50'
                        }`}
                      >
                        <span className="text-slate-500 shrink-0 select-none">#{ctxLog.id}</span>
                        <span className="text-slate-400 shrink-0 select-none">
                          {formatLocalTimestamp(ctxLog.timestamp, true, effectiveTimezone).slice(11, 19)}
                        </span>
                        <SeverityBadge severity={ctxLog.severity} className="shrink-0" />
                        <span className={`break-all ${isTarget ? 'text-slate-100 font-semibold' : 'text-slate-300'}`}>
                          {stripAnsi(ctxLog.message)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </SlideOver>
  );
};
