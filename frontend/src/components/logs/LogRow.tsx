import React from 'react';
import { Sparkles, CheckSquare, Square } from 'lucide-react';
import { LogEntry } from '../../types.ts';
import { SeverityBadge } from '../common/SeverityBadge.tsx';
import { stripAnsi, cleanLogMessageForDisplay } from '../../utils/formatters.ts';
import { formatLocalTimestamp } from './LiveLogStream.tsx';

export interface ProcessedLogEntry extends LogEntry {
  formattedTimestamp?: string;
  cleanedMessage?: string;
  strippedMessage?: string;
}

export interface LogRowProps {
  log: ProcessedLogEntry;
  index: number;
  isSelected: boolean;
  isHovered?: boolean;
  isMobile: boolean;
  style: React.CSSProperties;
  onSelect: (log: LogEntry, index: number, e: React.MouseEvent) => void;
  onClick: (log: LogEntry) => void;
  onDiagnoseAi: (logs: LogEntry[]) => void;
}

export function areLogRowPropsEqual(prevProps: LogRowProps, nextProps: LogRowProps): boolean {
  if (prevProps.log.id !== nextProps.log.id) return false;
  if (prevProps.isSelected !== nextProps.isSelected) return false;
  if (prevProps.isHovered !== nextProps.isHovered) return false;
  if (prevProps.index !== nextProps.index) return false;
  if (prevProps.isMobile !== nextProps.isMobile) return false;

  if (
    prevProps.style.transform !== nextProps.style.transform ||
    prevProps.style.height !== nextProps.style.height ||
    prevProps.style.top !== nextProps.style.top ||
    prevProps.style.left !== nextProps.style.left ||
    prevProps.style.width !== nextProps.style.width
  ) {
    return false;
  }

  if (prevProps.log !== nextProps.log) {
    if (
      prevProps.log.message !== nextProps.log.message ||
      prevProps.log.severity !== nextProps.log.severity ||
      prevProps.log.app_name !== nextProps.log.app_name ||
      prevProps.log.source_alias !== nextProps.log.source_alias ||
      prevProps.log.timestamp !== nextProps.log.timestamp ||
      prevProps.log.received_at !== nextProps.log.received_at ||
      prevProps.log.formattedTimestamp !== nextProps.log.formattedTimestamp ||
      prevProps.log.cleanedMessage !== nextProps.log.cleanedMessage ||
      prevProps.log.strippedMessage !== nextProps.log.strippedMessage
    ) {
      return false;
    }
  }

  return true;
}

export const LogRowComponent: React.FC<LogRowProps> = ({
  log,
  index,
  isSelected,
  isHovered,
  isMobile,
  style,
  onSelect,
  onClick,
  onDiagnoseAi,
}) => {
  const displayTimestamp = log.formattedTimestamp ?? formatLocalTimestamp(log.timestamp, log.received_at);
  const displayMessage = log.cleanedMessage ?? cleanLogMessageForDisplay(log.message);
  const displayTitle = log.strippedMessage ?? stripAnsi(log.message);

  if (isMobile) {
    return (
      <div
        data-index={index}
        onClick={() => onClick(log)}
        style={style}
        className={`log-row flex flex-col justify-between px-3 py-1.5 border-b border-dark-900 border-l-2 cursor-pointer text-[11px] leading-tight space-y-1 ${
          isSelected ? 'bg-accent-950/40 border-l-accent-500' : 'border-l-transparent'
        }`}
      >
        {/* Line 1: Severity Badge + App Name (Host Name) */}
        <div className="flex items-center gap-1.5 min-w-0">
          <SeverityBadge severity={log.severity} />
          <span className="font-mono text-xs truncate">
            <span className="text-slate-200 font-semibold">{log.app_name}</span>
            <span className="text-slate-400 font-normal ml-1">({log.source_alias})</span>
          </span>
        </div>

        {/* Line 2: Message Payload (break-all, 2 lines clamp) */}
        <div className="text-slate-200 text-xs font-mono break-all line-clamp-2 select-text leading-snug">
          {displayMessage}
        </div>

        {/* Line 3: Timestamp (left) + AI Action (right) */}
        <div className="flex items-center justify-between text-[11px] text-slate-500 font-mono">
          <span className="text-[10px] text-slate-400 font-mono shrink-0 select-none">
            {displayTimestamp}
          </span>
          <button
            onClick={(e) => {
              e.stopPropagation();
              onDiagnoseAi([log]);
            }}
            className="text-accent-400 hover:text-accent-300 p-1 flex items-center gap-1 shrink-0"
            title="Explain with AI"
          >
            <Sparkles className="w-3 h-3" />
            <span className="text-[10px] font-sans font-medium">AI</span>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      data-index={index}
      onClick={() => onClick(log)}
      style={style}
      className={`log-row grid grid-cols-[36px_165px_65px_130px_130px_1fr_60px] px-3 items-center border-b border-dark-900 border-l-2 hover:bg-dark-900/60 transition-colors cursor-pointer text-[11px] leading-tight ${
        isSelected ? 'bg-accent-950/40 border-l-accent-500' : 'border-l-transparent'
      }${isHovered ? ' bg-dark-900/60' : ''}`}
    >
      {/* Checkbox */}
      <div
        onClick={(e) => {
          e.stopPropagation();
          onSelect(log, index, e);
        }}
        className="h-full w-full flex items-center justify-center text-slate-500 hover:text-slate-200 cursor-pointer select-none"
      >
        {isSelected ? (
          <CheckSquare className="w-3.5 h-3.5 text-accent-400" />
        ) : (
          <Square className="w-3.5 h-3.5 opacity-40 hover:opacity-100" />
        )}
      </div>

      {/* Timestamp */}
      <div
        className="text-slate-400 truncate pr-2"
        title={`UTC: ${log.timestamp}\nReceived: ${log.received_at}`}
      >
        {displayTimestamp}
      </div>

      {/* Severity Badge */}
      <div>
        <SeverityBadge severity={log.severity} />
      </div>

      {/* Host Alias / IP */}
      <div className="text-slate-300 truncate pr-2" title={`${log.source_alias} (${log.source_ip})`}>
        {log.source_alias}
      </div>

      {/* App Name */}
      <div className="text-slate-400 truncate pr-2 font-medium" title={log.app_name}>
        {log.app_name}
      </div>

      {/* Raw Text Message without dangerouslySetInnerHTML */}
      <div className="text-slate-200 truncate pr-3 select-text" title={displayTitle}>
        {displayMessage}
      </div>

      {/* Quick Row Actions */}
      <div className="flex items-center justify-end gap-1 pr-1" onClick={(e) => e.stopPropagation()}>
        <button
          onClick={() => {
            onDiagnoseAi([log]);
          }}
          title="Explain with AI"
          className="p-1 text-slate-400 hover:text-accent-400 hover:bg-dark-800 rounded transition"
        >
          <Sparkles className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
};

export const LogRow = React.memo(LogRowComponent, areLogRowPropsEqual);
