import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Sparkles, Plus, Edit2, Layers, Terminal, FilterX, Trash2, AlertTriangle, RefreshCw, AlertCircle, ChevronUp, ChevronDown, BellPlus } from 'lucide-react';
import { LogEntry, DockerEndpoint } from '../../types.ts';
import { fetchLogContext, deleteSingleLog } from '../../api/logs.ts';
import { SeverityBadge } from '../common/SeverityBadge.tsx';
import { SlideOver } from '../common/SlideOver.tsx';
import { stripAnsi, cleanLogMessageForDisplay, formatLocalTimestamp, formatFullTimestamp, formatUtcOffset } from '../../utils/formatters.ts';
import { getCachedServerTimezone } from '../../api/settings.ts';
import { CopyButton } from '../common/CopyButton.tsx';

interface LogDetailModalProps {
  log: LogEntry | null;
  isOpen: boolean;
  onClose: () => void;
  onExplainWithAi: (log: LogEntry, contextLogs?: LogEntry[]) => void;
  onInspectWithContext?: (logs: LogEntry[]) => void;
  onAddAlias?: (ip: string) => void;
  onEditDockerHost?: (endpointId: number) => void;
  onManageDeletedDockerHost?: (info: { name: string; ip: string }) => void;
  dockerEndpoints?: DockerEndpoint[];
  /** Aliases of deleted or renamed Docker hosts; rows that carry one are container logs. */
  formerDockerAliases?: string[];
  isHostAliased?: boolean;
  onCreateDropRule?: (log: LogEntry) => void;
  /** Opens Rules & History with a new alert rule prefilled from this log. */
  onCreateAlertRule?: (log: LogEntry) => void;
  onDeleteLog?: (log: LogEntry) => void;
  /** Opens the newer log (the row above in the newest-first list). */
  onNavigateNewer?: () => void;
  /** Opens the older log (the row below in the newest-first list). */
  onNavigateOlder?: () => void;
  hasNewerLog?: boolean;
  hasOlderLog?: boolean;
  timeZone?: string;
  /** AI on shows the AI actions in full; AI off keeps one muted "Explain with AI" entry. */
  aiEnabled?: boolean;
}

const AI_OFF_TITLE = 'AI features are turned off in Settings';

/** True when a key press comes from a text field, where Up, Down, J and K must keep their usual meaning. */
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return target.isContentEditable || target.closest('[contenteditable]:not([contenteditable="false"])') !== null;
}

export const LogDetailModal: React.FC<LogDetailModalProps> = ({
  log,
  isOpen,
  onClose,
  onExplainWithAi,
  onInspectWithContext,
  onAddAlias,
  onEditDockerHost,
  onManageDeletedDockerHost,
  dockerEndpoints = [],
  formerDockerAliases = [],
  isHostAliased,
  onCreateDropRule,
  onCreateAlertRule,
  onDeleteLog,
  onNavigateNewer,
  onNavigateOlder,
  hasNewerLog,
  hasOlderLog,
  timeZone,
  aiEnabled = false,
}) => {
  const effectiveTimezone = timeZone || getCachedServerTimezone();
  // Each timestamp gets the offset in force at its own moment, so the badge stays right across a clock change
  const offsetBadge = (ts?: string | null) => {
    const offset = formatUtcOffset(ts, effectiveTimezone);
    return offset ? `(${offset})` : '';
  };
  const hostIsAliased = Boolean(
    isHostAliased ?? (log && log.source_alias && log.source_alias !== log.source_ip)
  );
  const [contextLogs, setContextLogs] = useState<LogEntry[]>([]);
  const [isLoadingContext, setIsLoadingContext] = useState(false);
  const [contextError, setContextError] = useState<string | null>(null);
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

  // Up / K opens the newer log and Down / J the older one, like the list order (newest first).
  // The latest handlers sit in a ref so the listener is not re-bound on every parent render.
  const drawerBodyRef = useRef<HTMLDivElement>(null);
  const navRef = useRef({ onNavigateNewer, onNavigateOlder, hasNewerLog, hasOlderLog });
  navRef.current = { onNavigateNewer, onNavigateOlder, hasNewerLog, hasOlderLog };

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
      const isNewer = e.key === 'ArrowUp' || e.key === 'k';
      const isOlder = e.key === 'ArrowDown' || e.key === 'j';
      if (!isNewer && !isOlder) return;
      if (isEditableTarget(e.target)) return;
      // Another dialog opened on top of the drawer (for example Create Drop Rule) owns the keys
      const body = drawerBodyRef.current;
      const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'));
      if (dialogs.some((d) => !body || !d.contains(body))) return;
      const nav = navRef.current;
      if (isNewer && nav.hasNewerLog && nav.onNavigateNewer) {
        e.preventDefault();
        nav.onNavigateNewer();
      } else if (isOlder && nav.hasOlderLog && nav.onNavigateOlder) {
        e.preventDefault();
        nav.onNavigateOlder();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
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

  const logId = log?.id;
  useEffect(() => {
    if (!(isOpen && logId !== undefined && showContext)) return;
    let cancelled = false;
    setContextLogs([]);
    setContextError(null);
    setIsLoadingContext(true);
    fetchLogContext(logId, 10, sameAppOnly)
      .then((res) => {
        if (!cancelled) setContextLogs(res.logs);
      })
      .catch((err) => {
        console.error('Failed to load log context', err);
        if (!cancelled) setContextError('Could not load context');
      })
      .finally(() => {
        if (!cancelled) setIsLoadingContext(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, logId, showContext, sameAppOnly]);

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

  // The API returns context oldest first; show it newest first, the same order as the log stream
  const contextLogsNewestFirst = useMemo(() => [...contextLogs].reverse(), [contextLogs]);

  const handleInspectTargetAndContext = () => {
    if (!log || targetAndContextLogs.length === 0) return;
    if (onInspectWithContext) {
      onInspectWithContext(targetAndContextLogs);
    } else {
      onExplainWithAi(log, targetAndContextLogs);
    }
    onClose();
  };

  const isSyslogRaw = useMemo(() => {
    if (!log?.raw) return false;
    return /^<\d{1,3}>/.test(log.raw);
  }, [log]);

  // Docker rows share source_ip with syslog and HTTP-ingested rows from the same machine, so
  // they are told apart by source_alias (matched exactly, as the backend does) or by the
  // reserved source_ip "docker" - never by facility, which HTTP ingest also defaults to 1
  const matchedDockerEndpoint = useMemo(() => {
    if (!log || isSyslogRaw) return null;
    const endpoints = dockerEndpoints || [];

    // 1. Match on source_alias
    if (log.source_alias) {
      const match = endpoints.find((ep) => ep.source_alias === log.source_alias);
      if (match) return match;
    }

    // 2. Local Docker match
    if (log.source_ip === 'docker') {
      const localMatch = endpoints.find(
        (ep) => ep.id === 1 || ep.url.includes('docker.sock')
      );
      if (localMatch) return localMatch;
    }

    return null;
  }, [log, isSyslogRaw, dockerEndpoints]);

  // A Docker host that is no longer configured: the reserved source_ip "docker", or the alias of a
  // deleted or renamed host (remote hosts log under their address, which syslog senders share)
  const isDeletedDockerHost = useMemo(() => {
    if (!log || isSyslogRaw || matchedDockerEndpoint) return false;
    return log.source_ip === 'docker' || (Boolean(log.source_alias) && formerDockerAliases.includes(log.source_alias));
  }, [log, isSyslogRaw, matchedDockerEndpoint, formerDockerAliases]);

  const isDockerSource = Boolean(matchedDockerEndpoint || isDeletedDockerHost);
  // HTTP-ingested logs have neither a syslog packet nor container output as their raw text
  const rawLabel = isDockerSource ? 'Raw Container Output' : isSyslogRaw ? 'Raw Syslog Packet' : 'Raw Log Record';
  const cleanedPayload = cleanLogMessageForDisplay(log?.message || '');

  if (!log) return null;

  return (
    <SlideOver
      isOpen={isOpen}
      onClose={onClose}
      title={`Log Record #${log.id} - ${log.source_alias}`}
      subtitle={
        (onNavigateNewer || onNavigateOlder) ? (
          <div className="flex items-center gap-1.5 pt-0.5">
            <button
              type="button"
              onClick={onNavigateNewer}
              disabled={!hasNewerLog}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-sm text-[11px] font-medium bg-raised hover:bg-raised-hover text-fg border border-line hover:border-line-strong transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed select-none"
              aria-label="Newer log"
              title="Newer log (Up or K)"
            >
              <ChevronUp className="w-3.5 h-3.5" />
              <span>Newer</span>
            </button>
            <button
              type="button"
              onClick={onNavigateOlder}
              disabled={!hasOlderLog}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-sm text-[11px] font-medium bg-raised hover:bg-raised-hover text-fg border border-line hover:border-line-strong transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed select-none"
              aria-label="Older log"
              title="Older log (Down or J)"
            >
              <ChevronDown className="w-3.5 h-3.5" />
              <span>Older</span>
            </button>
          </div>
        ) : undefined
      }
      width="max-w-3xl"
    >
      {/* On phones the message comes right after the actions; the timestamps and codes move below the raw record */}
      <div ref={drawerBodyRef} className="flex flex-col gap-4 text-xs font-sans">
        {/* Action Header */}
        <div className="flex flex-wrap items-center justify-between gap-2 p-3 bg-canvas rounded-lg border border-line">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={log.severity} />
            <span className="font-mono text-fg-soft font-medium">{log.app_name}</span>
            <span className="text-fg-subtle">•</span>
            <span className="font-mono text-fg-muted">{log.source_alias}</span>
            <span className="text-fg-muted">({log.source_ip})</span>
          </div>

          {/* Phones: two columns, with Explain with AI across both below them */}
          <div className="grid grid-cols-2 sm:flex sm:flex-row items-stretch sm:items-center gap-2 w-full sm:w-auto">
            {matchedDockerEndpoint && onEditDockerHost ? (
              <button
                type="button"
                onClick={() => {
                  onEditDockerHost(matchedDockerEndpoint.id);
                  onClose();
                }}
                className="flex items-center justify-center gap-1 px-2.5 py-1 bg-raised hover:bg-raised-strong text-fg border border-line-strong rounded-sm transition font-medium cursor-pointer"
              >
                <Edit2 className="w-3.5 h-3.5 text-accent-400" />
                <span>Edit Docker Host</span>
              </button>
            ) : isDeletedDockerHost && onManageDeletedDockerHost ? (
              <button
                type="button"
                onClick={() => {
                  onManageDeletedDockerHost({
                    name: log.source_alias,
                    ip: log.source_ip,
                  });
                  onClose();
                }}
                className="flex items-center justify-center gap-1 px-2.5 py-1 bg-raised hover:bg-raised-strong text-fg border border-line-strong rounded-sm transition font-medium cursor-pointer"
              >
                <Edit2 className="w-3.5 h-3.5 text-accent-400" />
                <span>Configure Docker Host</span>
              </button>
            ) : onAddAlias ? (
              hostIsAliased ? (
                <button
                  type="button"
                  onClick={() => {
                    onAddAlias(log.source_ip);
                    onClose();
                  }}
                  className="flex items-center justify-center gap-1 px-2.5 py-1 bg-raised hover:bg-raised-strong text-fg border border-line-strong rounded-sm transition font-medium cursor-pointer"
                >
                  <Edit2 className="w-3.5 h-3.5 text-accent-400" />
                  <span>Modify Host Alias</span>
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    onAddAlias(log.source_ip);
                    onClose();
                  }}
                  className="flex items-center justify-center gap-1 px-2.5 py-1 bg-raised hover:bg-raised-strong text-fg border border-line-strong rounded-sm transition font-medium cursor-pointer"
                >
                  <Plus className="w-3.5 h-3.5 text-accent-400" />
                  <span>Add Host Alias</span>
                </button>
              )
            ) : null}

            {onCreateDropRule && (
              <button
                onClick={() => onCreateDropRule(log)}
                className="flex items-center justify-center gap-1.5 px-2.5 py-1 bg-raised hover:bg-raised-strong text-fg border border-line-strong rounded-sm transition font-medium cursor-pointer"
                title="Create an ingestion drop rule for similar logs"
              >
                <FilterX className="w-3.5 h-3.5 text-accent-400" />
                <span>Create Drop Rule</span>
              </button>
            )}

            {onCreateAlertRule && (
              <button
                onClick={() => onCreateAlertRule(log)}
                className="flex items-center justify-center gap-1.5 px-2.5 py-1 bg-raised hover:bg-raised-strong text-fg border border-line-strong rounded-sm transition font-medium cursor-pointer"
                title="Create an alert rule for this app, with this log shown for reference"
              >
                <BellPlus className="w-3.5 h-3.5 text-accent-400" />
                <span>Create Alert Rule</span>
              </button>
            )}

            {onDeleteLog && (
              <button
                onClick={() => setShowDeleteConfirm(!showDeleteConfirm)}
                className="flex items-center justify-center gap-1.5 px-2.5 py-1 bg-raised hover:bg-danger-950 text-fg-soft hover:text-danger-300 border border-line-strong hover:border-danger-800 rounded-sm transition font-medium cursor-pointer"
                title="Delete this log record permanently"
              >
                <Trash2 className="w-3.5 h-3.5 text-danger-400" />
                <span>Delete</span>
              </button>
            )}

            {aiEnabled ? (
              <button
                onClick={() => onExplainWithAi(log)}
                className="col-span-2 flex items-center justify-center gap-1.5 px-3 py-1 bg-accent-700 hover:bg-accent-600 text-on-fill rounded-sm font-medium transition shadow-xs"
              >
                <Sparkles className="w-3.5 h-3.5" />
                <span>Explain with AI</span>
              </button>
            ) : (
              // Kept so AI stays discoverable: the AI modal explains how to turn it on
              <button
                onClick={() => onExplainWithAi(log)}
                title={AI_OFF_TITLE}
                className="col-span-2 flex items-center justify-center gap-1.5 px-2.5 py-1 bg-raised hover:bg-raised-strong text-fg-muted hover:text-fg-soft border border-line-strong rounded-sm font-medium transition cursor-pointer"
              >
                <Sparkles className="w-3.5 h-3.5 text-fg-subtle" />
                <span>Explain with AI</span>
              </button>
            )}
          </div>
        </div>

        {/* Delete Confirmation Banner */}
        {showDeleteConfirm && (
          <div className="p-3 bg-danger-950/70 border border-danger-800 rounded-lg flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-xs text-danger-200">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-danger-400 shrink-0" />
              <span>Permanently delete log record #{log.id}? This action cannot be reversed.</span>
            </div>
            <div className="flex items-center gap-2 self-end sm:self-auto">
              <button
                type="button"
                onClick={() => setShowDeleteConfirm(false)}
                disabled={isDeleting}
                className="px-2.5 py-1 text-xs text-fg-muted hover:text-fg hover:bg-raised rounded-sm transition cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDelete}
                disabled={isDeleting}
                className="px-3 py-1 text-xs bg-danger-600 hover:bg-danger-500 text-on-fill font-medium rounded-sm transition flex items-center gap-1 cursor-pointer"
              >
                {isDeleting ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                <span>Confirm Delete</span>
              </button>
            </div>
          </div>
        )}

        {deleteError && (
          <div className="p-3 bg-danger-950/60 border border-danger-800 rounded-lg flex items-start gap-2 text-xs text-danger-300">
            <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
            <span>{deleteError}</span>
          </div>
        )}


        {/* Structured Metadata Grid */}
        <div className="order-1 sm:order-0 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-2">
          <div className="bg-canvas p-2.5 rounded-sm border border-line">
            <span className="text-[10px] uppercase font-semibold text-fg-muted block mb-1">
              Timestamp {offsetBadge(log.timestamp)}
            </span>
            <span className="font-mono text-fg text-xs break-all select-all block">
              {formatFullTimestamp(log.timestamp, effectiveTimezone)}
            </span>
            <span className="text-[11px] font-mono text-fg-muted break-all select-all block mt-0.5" title="Universal Coordinated Time stored in database">
              UTC: {log.timestamp}
            </span>
          </div>
          <div className="bg-canvas p-2.5 rounded-sm border border-line">
            <span className="text-[10px] uppercase font-semibold text-fg-muted block mb-1">
              Received At {offsetBadge(log.received_at)}
            </span>
            <span className="font-mono text-fg text-xs break-all select-all block">
              {formatFullTimestamp(log.received_at, effectiveTimezone)}
            </span>
            <span className="text-[11px] font-mono text-fg-muted break-all select-all block mt-0.5" title="Universal Coordinated Time stored in database">
              UTC: {log.received_at}
            </span>
          </div>
          <div className="bg-canvas p-2.5 rounded-sm border border-line">
            <span className="text-[10px] uppercase font-semibold text-fg-muted block mb-1">Facility</span>
            <span className="font-mono text-fg text-xs break-all">{log.facility}</span>
          </div>
          <div className="bg-canvas p-2.5 rounded-sm border border-line">
            <span className="text-[10px] uppercase font-semibold text-fg-muted block mb-1">Severity Code</span>
            <span className="font-mono text-fg text-xs break-all">{log.severity}</span>
          </div>
        </div>

        {/* Formatted Message Box */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11px] font-semibold text-fg-soft uppercase tracking-wider flex items-center gap-1">
              <Terminal className="w-3.5 h-3.5 text-fg-muted" />
              <span>Message Payload</span>
            </span>
            <CopyButton
              text={cleanedPayload}
              label="Copy Message"
              title="Copy message payload"
              className="-mr-1"
            />
          </div>
          <div className="bg-canvas border border-line rounded-lg p-3 font-mono text-fg text-xs whitespace-pre-wrap break-all select-text max-h-60 overflow-y-auto">
            {/* Sanitized text element without dangerouslySetInnerHTML */}
            {cleanedPayload.trim() ? (
              cleanedPayload
            ) : (
              <span className="italic text-fg-subtle select-none">(empty message)</span>
            )}
          </div>
        </div>

        {/* Raw Syslog / Container Envelope */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[11px] font-semibold text-fg-soft uppercase tracking-wider">
              {rawLabel}
            </span>
            <CopyButton
              text={log.raw ?? ''}
              label="Copy Raw"
              title="Copy raw log record"
              className="-mr-1"
            />
          </div>
          <div className="bg-canvas border border-line rounded-lg p-3 font-mono text-fg-muted text-[11px] whitespace-pre-wrap break-all select-text max-h-36 overflow-y-auto">
            {stripAnsi(log.raw).trim() ? (
              stripAnsi(log.raw)
            ) : (
              <span className="italic text-fg-subtle select-none">(empty)</span>
            )}
          </div>
        </div>

        {/* Surrounding Context Inspector */}
        <div className="order-2 sm:order-0 pt-2 border-t border-line-subtle">
          <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
            <button
              onClick={() => setShowContext(!showContext)}
              className="flex items-center gap-1.5 font-medium text-fg-soft hover:text-accent-400 transition"
            >
              <Layers className="w-3.5 h-3.5" />
              <span>{showContext ? 'Hide Surrounding Context' : 'Load Surrounding Context (±5 Lines)'}</span>
            </button>

            {showContext && (
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-1.5 text-[11px] text-fg-muted cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={sameAppOnly}
                    onChange={(e) => setSameAppOnly(e.target.checked)}
                    className="rounded-sm bg-surface border-line text-accent-600 focus:ring-0 focus:ring-offset-0 w-3 h-3"
                  />
                  <span>Same App Only ({log.app_name})</span>
                </label>

                {aiEnabled && contextLogs.length > 0 && !isLoadingContext && (
                  <button
                    onClick={handleInspectTargetAndContext}
                    title="Add Context to AI Analysis"
                    className="flex items-center gap-1.5 px-2.5 py-1 bg-accent-700 hover:bg-accent-600 text-on-fill rounded-sm font-medium transition text-[11px] shadow-xs"
                  >
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>Inspect Target + Context ({targetAndContextLogs.length} logs)</span>
                  </button>
                )}
              </div>
            )}
          </div>

          {showContext && (
            <div className="bg-canvas border border-line rounded-lg overflow-hidden">
              {isLoadingContext ? (
                <div className="p-4 text-center text-fg-muted font-mono">Loading surrounding context...</div>
              ) : contextError ? (
                <div role="alert" className="p-4 text-center text-danger-400 font-mono">{contextError}</div>
              ) : contextLogs.length === 0 ? (
                <div className="p-4 text-center text-fg-muted font-mono">No surrounding logs found.</div>
              ) : (
                <div className="font-mono text-[11px] divide-y divide-line-subtle max-h-80 overflow-y-auto">
                  {contextLogsNewestFirst.map((ctxLog) => {
                    const isTarget = ctxLog.id === log.id;
                    return (
                      <div
                        key={ctxLog.id}
                        className={`p-2 flex items-start gap-2 ${
                          isTarget ? 'bg-accent-950/40 border-l-2 border-accent-500' : 'hover:bg-surface/50'
                        }`}
                      >
                        <span className="text-fg-muted shrink-0 select-none">#{ctxLog.id}</span>
                        <span className="text-fg-muted shrink-0 select-none">
                          {formatLocalTimestamp(ctxLog.timestamp, true, effectiveTimezone).slice(11, 19)}
                        </span>
                        <SeverityBadge severity={ctxLog.severity} className="shrink-0" />
                        {/* With "Same App Only" every row shares the app, so the column is left out */}
                        {!sameAppOnly && (
                          <span
                            data-testid="context-app-name"
                            className="text-fg-muted shrink-0 w-24 truncate"
                            title={ctxLog.app_name}
                          >
                            {ctxLog.app_name}
                          </span>
                        )}
                        <span className={`break-all ${isTarget ? 'text-fg-strong font-semibold' : 'text-fg-soft'}`}>
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
