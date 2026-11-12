import React from 'react';
import { AlertCircle, Sparkles, Trash2 } from 'lucide-react';
import { LogEntry } from '../../types.ts';
import { SELECT_ALL_LIMIT } from './useLogSelection.ts';

interface SelectionActionBarProps {
  selectedLogs: LogEntry[];
  aiEnabled: boolean;
  onDeselectAll: () => void;
  onDelete: () => void;
  onAnalyze: () => void;
}

/** The bar under the list while rows are ticked: how many and from which hosts, then the actions. */
export const SelectionActionBar: React.FC<SelectionActionBarProps> = ({
  selectedLogs,
  aiEnabled,
  onDeselectAll,
  onDelete,
  onAnalyze,
}) => {
  const uniqueHosts = Array.from(new Set(selectedLogs.map((l) => l.source_alias || l.source_ip || 'unknown')));
  const overAiLimit = selectedLogs.length > SELECT_ALL_LIMIT;

  return (
    <div className="bg-dark-900 border-t border-dark-700 px-3 sm:px-4 py-2 sm:py-2.5 flex flex-wrap items-center justify-between gap-2 select-none z-20 shadow-2xl animate-in slide-in-from-bottom">
      <div className="flex items-center gap-2 sm:gap-3 min-w-0">
        <span className="text-xs font-semibold text-slate-200 shrink-0">
          {selectedLogs.length} log{selectedLogs.length === 1 ? '' : 's'} selected
        </span>
        {uniqueHosts.length === 1 ? (
          <span className="text-xs text-slate-400 font-mono truncate">
            Host: <span className="text-accent-400">{uniqueHosts[0]}</span>
          </span>
        ) : (
          <span className="text-xs text-slate-400 font-mono truncate">
            Hosts:{' '}
            <span className="text-accent-400">
              {uniqueHosts.length} hosts ({uniqueHosts.slice(0, 3).join(', ')}
              {uniqueHosts.length > 3 ? '...' : ''})
            </span>
          </span>
        )}
      </div>

      <div className="flex items-center gap-2 shrink-0">
        {aiEnabled && overAiLimit && (
          <span className="text-xs text-amber-400 flex items-center gap-1 font-mono">
            <AlertCircle className="w-3.5 h-3.5 text-amber-400" />
            <span className="hidden sm:inline">Max {SELECT_ALL_LIMIT} logs for AI</span>
          </span>
        )}

        <button
          onClick={onDeselectAll}
          className="px-2.5 py-1 text-xs text-slate-300 bg-dark-800 hover:text-white hover:bg-dark-700 border border-dark-600 rounded transition cursor-pointer font-medium"
        >
          Deselect All
        </button>

        <button
          onClick={onDelete}
          className="text-white text-xs font-medium px-3.5 py-1.5 rounded-lg flex items-center gap-1.5 transition shadow-md cursor-pointer bg-red-600 hover:bg-red-500"
        >
          <Trash2 className="w-3.5 h-3.5" />
          <span>Delete ({selectedLogs.length})</span>
        </button>

        {aiEnabled && (
          <button
            onClick={onAnalyze}
            className={`text-white text-xs font-medium px-3.5 py-1.5 rounded-lg flex items-center gap-1.5 transition shadow-md cursor-pointer ${
              overAiLimit ? 'bg-amber-600 hover:bg-amber-500' : 'bg-accent-700 hover:bg-accent-600'
            }`}
          >
            <Sparkles className="w-3.5 h-3.5" />
            <span>Run Analysis ({selectedLogs.length})</span>
          </button>
        )}
      </div>
    </div>
  );
};
