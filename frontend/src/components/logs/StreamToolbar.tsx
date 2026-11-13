import React from 'react';
import { Bookmark, Box, Clock, Pause, Play, Server, Trash2 } from 'lucide-react';
import { LogFilterParams, SavedView } from '../../types.ts';
import { SELECT_ALL_LIMIT } from './useLogSelection.ts';

interface StreamToolbarProps {
  isMobile: boolean;
  /** Saved views pinned to the bar, and the one the filters match */
  pinnedViews: SavedView[];
  activeSavedView: SavedView | null | undefined;
  onApplySavedView: (params: LogFilterParams) => void;
  onResetFilters: () => void;
  hostChips: string[];
  appChips: string[];
  activeHosts: string[];
  activeApps: string[];
  /** Hosts the selected apps leave selectable; other host chips show disabled */
  selectableHosts: Set<string>;
  onToggleHost: (host: string) => void;
  onToggleApp: (app: string) => void;
  bufferSize: number;
  hasSelection: boolean;
  onSelectAll: () => void;
  onDeselectAll: () => void;
  /** True when an end time is set: the live stream is off */
  historicalRange: boolean;
  autoScroll: boolean;
  onToggleAutoScroll: () => void;
  onClearBuffer: () => void;
}

/**
 * Quick filter chips and stream controls: a band under the search bar on desktop, and on phones
 * part of the search bar's second line, next to Filters, to save a row.
 */
export const StreamToolbar: React.FC<StreamToolbarProps> = ({
  isMobile,
  pinnedViews,
  activeSavedView,
  onApplySavedView,
  onResetFilters,
  hostChips,
  appChips,
  activeHosts,
  activeApps,
  selectableHosts,
  onToggleHost,
  onToggleApp,
  bufferSize,
  hasSelection,
  onSelectAll,
  onDeselectAll,
  historicalRange,
  autoScroll,
  onToggleAutoScroll,
  onClearBuffer,
}) => (
  <>
    {/* Left: Quick Filter Pills */}
    <div className="relative flex-1 min-w-0 flex items-center overflow-hidden">
      <div className="flex items-center gap-1.5 overflow-x-auto py-0.5 pr-8 scrollbar-none [&::-webkit-scrollbar]:hidden max-w-full">
        {/* On phones the Filters button that comes first on the line does the labelling */}
        {!isMobile && <span className="text-fg-muted font-medium text-[11px] shrink-0">Quick Filters:</span>}
        {/* Pinned saved views first: a click applies the view, as the Views menu does, and a
            click on the active view clears its filters again, like a host or app chip */}
        {pinnedViews.map((view) => {
          const isActive = activeSavedView?.id === view.id;
          return (
            <button
              key={`view-${view.id}`}
              type="button"
              data-testid="quick-filter-view"
              onClick={() => (isActive ? onResetFilters() : onApplySavedView(view.query_params))}
              aria-pressed={isActive}
              title={isActive ? `Clear saved view ${view.name}` : `Apply saved view ${view.name}`}
              className={`flex items-center gap-1 px-2 py-1 md:py-0.5 rounded text-[11px] border transition cursor-pointer shrink-0 ${
                isActive
                  ? 'bg-accent-950 text-accent-300 border-accent-500 font-semibold'
                  : 'bg-raised text-fg-soft border-line hover:border-line-stronger'
              }`}
            >
              <Bookmark className={`w-3 h-3 text-accent-400 ${isActive ? 'fill-accent-400' : ''}`} aria-hidden="true" />
              <span className="truncate max-w-40">{view.name}</span>
            </button>
          );
        })}
        {hostChips.length > 0 && (
          <span className={`flex items-center gap-1 text-fg-muted text-[10px] font-medium uppercase tracking-wide shrink-0 select-none ${pinnedViews.length > 0 ? 'ml-1' : ''}`}>
            <Server className="w-3 h-3" aria-hidden="true" />
            <span>Hosts</span>
          </span>
        )}
        {hostChips.map((src) => {
          const isSelected = activeHosts.includes(src);
          const isDisabled = !isSelected && !selectableHosts.has(src);
          return (
            <button
              key={src}
              data-testid="quick-filter-host"
              onClick={() => onToggleHost(src)}
              disabled={isDisabled}
              title={isDisabled ? `No logs from ${src} for the selected app${activeApps.length > 1 ? 's' : ''}` : undefined}
              className={`px-2 py-1 md:py-0.5 rounded text-[11px] font-mono border transition shrink-0 ${
                isSelected
                  ? 'bg-accent-950 text-accent-300 border-accent-700 font-semibold cursor-pointer'
                  : isDisabled
                  ? 'bg-raised text-fg-soft border-line opacity-40 cursor-not-allowed'
                  : 'bg-raised text-fg-soft border-line hover:border-line-stronger cursor-pointer'
              }`}
            >
              {src}
            </button>
          );
        })}
        {appChips.length > 0 && (
          <span className="flex items-center gap-1 text-fg-muted text-[10px] font-medium uppercase tracking-wide shrink-0 select-none ml-1">
            <Box className="w-3 h-3" aria-hidden="true" />
            <span>Apps</span>
          </span>
        )}
        {appChips.map((app) => {
          const isSelected = activeApps.includes(app);
          return (
            <button
              key={app}
              data-testid="quick-filter-app"
              onClick={() => onToggleApp(app)}
              className={`px-2 py-1 md:py-0.5 rounded text-[11px] font-mono border transition cursor-pointer shrink-0 ${
                isSelected
                  ? 'bg-accent-950 text-accent-300 border-accent-700 font-semibold'
                  : 'bg-raised text-fg-soft border-line hover:border-line-stronger'
              }`}
            >
              {app}
            </button>
          );
        })}
      </div>
      {/* Fade overlay on the right edge of quick filters */}
      <div className="pointer-events-none absolute right-0 top-0 bottom-0 w-8 bg-linear-to-r from-transparent to-surface z-10" />
    </div>

    {/* Right: Stream State Controls */}
    <div className="flex items-center gap-2.5 shrink-0 ml-auto bg-surface relative z-20 pl-1">
      {/* md, not sm: below 768px the table becomes cards, which have no multi-select */}
      <div data-testid="select-all-group" className="hidden md:flex items-center gap-1.5 border-r border-line pr-3 mr-1">
        <button
          onClick={onSelectAll}
          disabled={bufferSize === 0}
          className="px-2 py-1 rounded-sm text-xs font-medium bg-raised text-fg-soft border border-line hover:border-line-strongest hover:text-fg-max transition disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
          title={bufferSize > SELECT_ALL_LIMIT ? `Select all logs in buffer (capped at ${SELECT_ALL_LIMIT})` : 'Select all logs in buffer'}
        >
          Select All
        </button>
        {hasSelection && (
          <button
            onClick={onDeselectAll}
            className="px-2 py-1 rounded-sm text-xs font-medium bg-raised text-fg-muted border border-line hover:border-line-strongest hover:text-fg transition cursor-pointer"
            title="Deselect all selected logs"
          >
            Deselect All
          </button>
        )}
      </div>

      <span className="hidden sm:inline font-mono text-fg-muted text-[11px]">
        Screen Buffer: <span className="text-fg">{bufferSize.toLocaleString()}</span> lines
      </span>

      {historicalRange ? (
        <span
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-sm text-xs font-medium bg-warning-950/50 text-warning-300 border border-warning-800/70 font-mono select-none"
          title="A historical end-time (To) is set. Live stream is paused to preserve historical boundaries."
        >
          <Clock className="w-3.5 h-3.5 text-warning-400" />
          <span>{isMobile ? 'History' : 'Historical Range (Stream Paused)'}</span>
        </span>
      ) : (
        <button
          onClick={onToggleAutoScroll}
          title={autoScroll ? 'Auto-scroll is on: click to pause' : 'Paused: click to resume auto-scroll'}
          className={`flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium border transition ${
            autoScroll
              ? 'bg-raised text-success-400 border-success-900/60 hover:bg-raised-strong'
              : 'bg-warning-950/60 text-warning-300 border-warning-800 hover:bg-warning-900/60'
          }`}
        >
          {autoScroll ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
          {/* Short label on phones, where the bar is narrow */}
          <span>{autoScroll ? (isMobile ? 'Scroll' : 'Auto-Scroll ON') : 'Paused'}</span>
        </button>
      )}

      {/* Left out on phones to leave room for the quick filters */}
      {!isMobile && (
        <button
          onClick={onClearBuffer}
          title="Clear screen buffer (clears browser view only; does not delete logs from disk)"
          aria-label="Clear screen buffer (clears browser view only; does not delete logs from disk)"
          className="p-1 text-fg-muted hover:text-danger-400 hover:bg-raised rounded-sm transition cursor-pointer"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  </>
);
