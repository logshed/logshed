import React from 'react';
import { Sparkles, CheckSquare, Square } from 'lucide-react';
import { LogEntry } from '../../types.ts';
import { SeverityBadge } from '../common/SeverityBadge.tsx';

export interface ProcessedLogEntry extends LogEntry {
  /** source_alias as stored, before prepareLogEntry shows a syslog host alias in its place. */
  storedSourceAlias?: string;
  formattedTimestamp: string;
  cleanedMessage: string;
  strippedMessage: string;
}

// Desktop column layout, shared by the table header and every row so they stay aligned.
// Without AI the last (row actions) column is dropped.
export const LOG_GRID_COLS_WITH_AI = 'grid-cols-[36px_120px_65px_130px_130px_1fr_60px]';
export const LOG_GRID_COLS_WITHOUT_AI = 'grid-cols-[36px_120px_65px_130px_130px_1fr]';

export function logGridCols(aiEnabled: boolean): string {
  return aiEnabled ? LOG_GRID_COLS_WITH_AI : LOG_GRID_COLS_WITHOUT_AI;
}

// Fixed row heights for the virtualized list. The mobile card is 8px of padding, a 20px header line,
// a 32px two-line message, a 16px footer and two 4px gaps; every line has a fixed height so the
// message is never squeezed into a partly visible second line.
export const DESKTOP_LOG_ROW_HEIGHT = 28;
export const MOBILE_LOG_ROW_HEIGHT = 84;

/** DOM id of a row, for the list's aria-activedescendant. */
export function logRowId(logId: number): string {
  return `log-row-${logId}`;
}

// The keyboard cursor's outline, shown only while the log list has focus
const CURSOR_CLASS = ' group-focus/loglist:ring-1 group-focus/loglist:ring-inset group-focus/loglist:ring-accent-400';

/** Background and left border: the alert's log (amber) wins over a selected row (accent). */
function rowStateClass(isSelected: boolean, isFocused: boolean): string {
  if (isFocused) return 'bg-amber-950/50 border-l-amber-400';
  if (isSelected) return 'bg-accent-950/40 border-l-accent-500';
  return 'border-l-transparent';
}

/** Desktop hover: brightens a highlighted row rather than replacing its colour, so it still looks selected. */
function rowHoverClass(isSelected: boolean, isFocused: boolean): string {
  if (isFocused) return 'hover:bg-amber-900/50';
  if (isSelected) return 'hover:bg-accent-900/50';
  return 'hover:bg-dark-900/60';
}

export interface LogRowProps {
  log: ProcessedLogEntry;
  index: number;
  isSelected: boolean;
  isMobile: boolean;
  /** Shows the "Explain with AI" row action (a pointer target); off until settings say AI is on. */
  aiEnabled?: boolean;
  /** Highlights the row as the log an alert link pointed at (the log that triggered the alert). */
  isFocused?: boolean;
  /** Outlines the row as the list's keyboard cursor (only while the list has focus). */
  isCursor?: boolean;
  style: React.CSSProperties;
  /** Ticks or unticks the row; shiftKey ticks the range from the last ticked row. */
  onSelect: (log: LogEntry, index: number, shiftKey: boolean) => void;
  onClick: (log: LogEntry) => void;
  onDiagnoseAi: (logs: LogEntry[]) => void;
}

export function areLogRowPropsEqual(prevProps: LogRowProps, nextProps: LogRowProps): boolean {
  if (prevProps.log.id !== nextProps.log.id) return false;
  if (prevProps.isSelected !== nextProps.isSelected) return false;
  if (prevProps.index !== nextProps.index) return false;
  if (prevProps.isMobile !== nextProps.isMobile) return false;
  if (Boolean(prevProps.aiEnabled) !== Boolean(nextProps.aiEnabled)) return false;
  if (Boolean(prevProps.isFocused) !== Boolean(nextProps.isFocused)) return false;
  if (Boolean(prevProps.isCursor) !== Boolean(nextProps.isCursor)) return false;

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
  isMobile,
  aiEnabled = false,
  isFocused = false,
  isCursor = false,
  style,
  onSelect,
  onClick,
  onDiagnoseAi,
}) => {
  const displayTimestamp = log.formattedTimestamp;
  const displayMessage = log.cleanedMessage;
  const displayTitle = log.strippedMessage;

  if (isMobile) {
    return (
      <div
        id={logRowId(log.id)}
        role="option"
        aria-selected={isSelected}
        data-index={index}
        data-focused={isFocused || undefined}
        onClick={() => onClick(log)}
        style={style}
        className={`log-row flex flex-col px-3 py-1 border-b border-dark-700 border-l-2 cursor-pointer text-[11px] leading-tight space-y-1 ${
          rowStateClass(isSelected, isFocused)
        }${isCursor ? CURSOR_CLASS : ''}`}
      >
        {/* Line 1: Severity Badge + App Name (Host Name) */}
        <div className="flex items-center gap-1.5 min-w-0 h-5 shrink-0">
          <SeverityBadge severity={log.severity} />
          <span className="font-mono text-xs truncate">
            <span className="text-slate-200 font-semibold">{log.app_name}</span>
            <span className="text-slate-400 font-normal ml-1">({log.source_alias})</span>
          </span>
        </div>

        {/* Line 2: Message Payload (break-all, 2 lines clamp, always two full lines of height) */}
        <div className="text-slate-200 text-xs font-mono break-all line-clamp-2 select-text leading-4 h-8 shrink-0">
          {displayMessage.trim() ? (
            displayMessage
          ) : (
            <span className="italic text-slate-500 select-none">(empty message)</span>
          )}
        </div>

        {/* Line 3: Timestamp (left) + AI Action (right, only when AI is on) */}
        <div className="flex items-center justify-between h-4 shrink-0 text-[11px] text-slate-400 font-mono">
          <span className="text-[10px] text-slate-400 font-mono shrink-0 select-none">
            {displayTimestamp}
          </span>
          {aiEnabled && (
            // A touch target, 32px tall; the negative margin keeps the 16px footer height unchanged.
            // Not a control: see the desktop row's action below.
            <div
              aria-hidden="true"
              onClick={(e) => {
                e.stopPropagation();
                onDiagnoseAi([log]);
              }}
              className="text-accent-400 hover:text-accent-300 min-h-8 px-2 -my-2 flex items-center gap-1 shrink-0 cursor-pointer select-none"
              title="Explain with AI"
            >
              <Sparkles className="w-3 h-3" />
              <span className="text-[10px] font-sans font-medium">AI</span>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div
      id={logRowId(log.id)}
      role="option"
      aria-selected={isSelected}
      data-index={index}
      data-focused={isFocused || undefined}
      onClick={() => onClick(log)}
      style={style}
      className={`log-row grid ${logGridCols(aiEnabled)} px-3 items-center border-b border-dark-900 border-l-2 transition-colors cursor-pointer text-[11px] leading-tight ${
        rowStateClass(isSelected, isFocused)
      } ${rowHoverClass(isSelected, isFocused)}${isCursor ? CURSOR_CLASS : ''}`}
    >
      {/* Checkbox: a mouse target; keyboard users tick the cursor row with Space, and the row's
          aria-selected tells screen readers */}
      <div
        data-testid="log-row-checkbox"
        aria-hidden="true"
        onClick={(e) => {
          e.stopPropagation();
          onSelect(log, index, e.shiftKey);
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
        {displayMessage.trim() ? (
          displayMessage
        ) : (
          <span className="italic text-slate-500 select-none">(empty message)</span>
        )}
      </div>

      {/* Quick Row Actions (AI only, so the column is dropped when AI is off). Like the checkbox, a
          mouse target and not a control: a list option may not contain one. Keyboard and screen reader
          users open the log (Enter) and use "Explain with AI" in its drawer, or tick rows (Space) and
          use the toolbar's AI action. */}
      {aiEnabled && (
        <div className="flex items-center justify-end gap-1 pr-1" onClick={(e) => e.stopPropagation()}>
          <div
            aria-hidden="true"
            onClick={() => {
              onDiagnoseAi([log]);
            }}
            title="Explain with AI"
            className="p-1 text-slate-400 hover:text-accent-400 hover:bg-dark-800 rounded transition cursor-pointer"
          >
            <Sparkles className="w-3 h-3" />
          </div>
        </div>
      )}
    </div>
  );
};

export const LogRow = React.memo(LogRowComponent, areLogRowPropsEqual);
