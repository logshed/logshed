import React, { useState, useMemo, useEffect, useCallback, useRef, useId } from 'react';
import { Search, RotateCcw, Filter, Clock, X, SlidersHorizontal, CircleHelp } from 'lucide-react';
import { LogFilterParams, SavedView } from '../../types.ts';
import { MultiSelectDropdown } from '../common/MultiSelectDropdown.tsx';
import { SlideOver } from '../common/SlideOver.tsx';
import { SEVERITIES } from '../common/SeverityBadge.tsx';
import { moreSevereLabel, compactMoreSevereLabel } from '../../utils/severityLabels.ts';
import {
  toLocalDatetimeInputString,
  fromLocalDatetimeInputString,
  startOfTodayIso,
  timeInputZoneLabel,
} from '../../utils/formatters.ts';
import { SavedViewsMenu, SaveViewModal } from './SavedViewsMenu.tsx';
import { SavedViewsState, useSavedViews, findActiveView } from './useSavedViews.ts';

// Time presets are stored as relative values (the backend resolves them at query time), so a saved
// view or shared link keeps meaning "the last hour" instead of freezing the moment it was made.
const TIME_PRESET_MS: Record<string, number> = {
  '15m': 15 * 60 * 1000,
  '1h': 60 * 60 * 1000,
  '6h': 6 * 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
};

/** Note for a host or app list the facets endpoint capped; the limit comes from the response. */
function facetsNote(kind: 'hosts' | 'apps', limit?: number): string {
  const shown = limit ? `the first ${limit}` : 'only some';
  return `Showing ${shown} ${kind}. Type a name to add others.`;
}

export const SEARCH_PLACEHOLDER = 'Search logs, e.g. "disk full", error OR timeout, app:nginx';

/** The search syntax listed in the help popover; each entry works in the history search. */
export const SEARCH_SYNTAX_HELP: { example: string; text: string }[] = [
  { example: 'disk time', text: 'Every word must match, as the start of a word (time finds timeout).' },
  { example: '"exact phrase"', text: 'The words in this order.' },
  { example: 'error OR timeout', text: 'Either word.' },
  { example: 'auth NOT cron', text: 'The first word without the second. OR and NOT work in any case.' },
  { example: 'app:nginx', text: 'App or container name.' },
  { example: 'host:pve-node2', text: 'Host name (host alias).' },
  { example: 'msg:"disk full"', text: 'Message text only.' },
];

// 'since_midnight' is a relative preset too: the backend resolves it as 00:00 today in the server
// time zone, the zone the UI shows times in.
const SINCE_MIDNIGHT = 'since_midnight';
const isTimePreset = (from?: string): from is string =>
  Boolean(from) && (from === SINCE_MIDNIGHT || Object.prototype.hasOwnProperty.call(TIME_PRESET_MS, from as string));

/** The time preset matching the filters: 'all', a relative preset such as '1h', or 'custom' for absolute bounds. */
export function deriveTimePreset(from?: string, to?: string): string {
  if (!from && !to) return 'all';
  if (from && !to && isTimePreset(from)) return from;
  return 'custom';
}

/**
 * Which single-value controls are filtering. The Reset count and the highlighted controls both read
 * this, so they cannot disagree. Time is active for any preset but All Time, or a From or To date.
 */
export function activeFilterControls(filters: LogFilterParams, timePreset: string) {
  return {
    query: Boolean(filters.query && filters.query.trim()),
    severity: filters.severity_max !== undefined && filters.severity_max !== null,
    time: timePreset !== 'all' || Boolean(filters.from) || Boolean(filters.to),
  };
}

// The active look of the Host and App dropdowns (MultiSelectDropdown), reused for the other controls.
// The box sets the text colour the value inherits where the theme has no accent-300 shade, as the
// dropdown button does.
const ACTIVE_BORDER = 'border-accent-600/60';
const IDLE_BORDER = 'border-dark-700';
const ACTIVE_BOX_TEXT = 'text-slate-100';
const ACTIVE_VALUE = 'text-accent-300 font-medium';
const CLEAR_BUTTON = 'text-slate-500 hover:text-slate-200 rounded hover:bg-dark-800 transition cursor-pointer shrink-0';

interface LogSearchBarProps {
  filters: LogFilterParams;
  onFilterChange: (filters: LogFilterParams) => void;
  onSearch: () => void;
  onReset: () => void;
  availableSources?: string[];
  availableApps?: string[];
  onApplySavedView?: (filters: LogFilterParams) => void;
  /**
   * Saved views shared with the Quick Filters bar (from useSavedViews). Without it the search bar
   * loads its own list.
   */
  savedViews?: SavedViewsState;
  totalCount?: number;
  totalCapped?: boolean;
  /** True when the host and app lists were capped by the facets endpoint. */
  facetsTruncated?: boolean;
  /** Most entries the facets endpoint returns per list (its `limit` field). */
  facetsLimit?: number;
  /**
   * Shown after the Filters, count and Reset controls on the second line on phones, which then
   * becomes a compact toolbar band (the Console passes its quick filters and stream controls).
   */
  mobileToolbar?: React.ReactNode;
  /** The server time zone: custom From and To times are read and shown on its clock, as every log time is. */
  timeZone?: string;
}

export const LogSearchBar: React.FC<LogSearchBarProps> = React.memo(({
  filters,
  onFilterChange,
  onSearch,
  onReset,
  availableSources = [],
  availableApps = [],
  onApplySavedView,
  savedViews: sharedSavedViews,
  totalCount,
  totalCapped = false,
  facetsTruncated = false,
  facetsLimit,
  mobileToolbar,
  timeZone,
}) => {
  const fieldId = useId();
  // The selected preset follows the filters (saved views, links, Reset); "Custom" chosen before any
  // bounds are entered is the only local choice.
  const [customTimeRequested, setCustomTimeRequested] = useState<boolean>(false);
  const derivedTimePreset = deriveTimePreset(filters.from, filters.to);
  const timePreset = customTimeRequested ? 'custom' : derivedTimePreset;
  const showCustomTime = timePreset === 'custom';
  useEffect(() => {
    if (derivedTimePreset !== 'all' && derivedTimePreset !== 'custom') setCustomTimeRequested(false);
  }, [derivedTimePreset]);
  const [isMobileDrawerOpen, setIsMobileDrawerOpen] = useState<boolean>(false);
  const [isSaveModalOpen, setIsSaveModalOpen] = useState<boolean>(false);

  const ownSavedViews = useSavedViews(!sharedSavedViews);
  const savedViewsState = sharedSavedViews ?? ownSavedViews;
  const { views: savedViews, isLoading: isViewsLoading, error: viewsError } = savedViewsState;
  const { togglePin, deleteView, renameView, createView } = savedViewsState;
  const activeView = useMemo(() => findActiveView(savedViews, filters), [savedViews, filters]);

  const handleTogglePin = useCallback((e: React.MouseEvent, view: SavedView) => {
    e.stopPropagation();
    void togglePin(view);
  }, [togglePin]);

  const handleDeleteSavedView = useCallback((e: React.MouseEvent, viewId: number) => {
    e.stopPropagation();
    void deleteView(viewId);
  }, [deleteView]);

  const handleRenameView = useCallback(async (view: SavedView, name: string) => {
    await renameView(view.id, name);
  }, [renameView]);

  const handleSaveViewSubmit = useCallback(async (name: string, isPinned: boolean) => {
    await createView(name, filters, isPinned);
  }, [createView, filters]);

  // Search syntax help popover: closes on Esc, a click outside, or focus leaving it
  const [isHelpOpen, setIsHelpOpen] = useState<boolean>(false);
  const helpRef = useRef<HTMLDivElement>(null);
  const helpButtonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!isHelpOpen) return;
    const handleMouseDown = (event: MouseEvent) => {
      if (helpRef.current && !helpRef.current.contains(event.target as Node)) setIsHelpOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsHelpOpen(false);
        helpButtonRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isHelpOpen]);
  const handleHelpBlur = useCallback((e: React.FocusEvent<HTMLDivElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setIsHelpOpen(false);
  }, []);

  // Compute active sources as an array
  const activeSources: string[] = useMemo(() => {
    if (filters.sources && Array.isArray(filters.sources)) return filters.sources;
    if (filters.source) {
      if (Array.isArray(filters.source)) return filters.source;
      return filters.source.split(',').map((s) => s.trim()).filter(Boolean);
    }
    return [];
  }, [filters.sources, filters.source]);

  // Compute active apps as an array
  const activeApps: string[] = useMemo(() => {
    if (filters.apps && Array.isArray(filters.apps)) return filters.apps;
    if (filters.app_name) {
      if (Array.isArray(filters.app_name)) return filters.app_name;
      return filters.app_name.split(',').map((a) => a.trim()).filter(Boolean);
    }
    return [];
  }, [filters.apps, filters.app_name]);

  const active = activeFilterControls(filters, timePreset);

  // Count active filters to conditionally show Reset button with badge
  const activeFilterCount =
    Number(active.query) + Number(active.severity) + Number(active.time) + activeSources.length + activeApps.length;

  const hasActiveFilters = activeFilterCount > 0;

  const handleTimePresetChange = useCallback((preset: string) => {
    if (preset === 'custom') {
      setCustomTimeRequested(true);
      // Start the pickers from the preset's current window rather than leaving them empty
      if (filters.from === SINCE_MIDNIGHT) {
        // "Today" starts at midnight on the server clock, as the backend reads since_midnight
        onFilterChange({ ...filters, from: startOfTodayIso(timeZone) });
      } else if (filters.from && Object.prototype.hasOwnProperty.call(TIME_PRESET_MS, filters.from)) {
        onFilterChange({
          ...filters,
          from: new Date(Date.now() - TIME_PRESET_MS[filters.from]).toISOString(),
        });
      }
      return;
    }

    setCustomTimeRequested(false);
    if (preset === 'all') {
      onFilterChange({ ...filters, from: undefined, to: undefined });
      return;
    }

    onFilterChange({
      ...filters,
      from: preset,
      to: undefined,
    });
  }, [filters, onFilterChange, timeZone]);

  const handleClearSeverity = () => onFilterChange({ ...filters, severity_max: undefined });
  const handleClearTime = () => handleTimePresetChange('all');

  const handleResetFilters = useCallback(() => {
    setCustomTimeRequested(false);
    onReset();
  }, [onReset]);

  return (
    <div className="bg-dark-950 border-b border-dark-700 p-2.5 flex flex-col gap-2 select-none text-xs shrink-0">
      {/* Top Row: Search Input & Primary Search Actions. On phones the input and the search
          button get a line of their own, so the query stays readable while typing. */}
      <div className="flex flex-col md:flex-row md:items-center gap-2">
        <div className="flex items-center gap-2 flex-1 min-w-0">
          {/* FTS Search Input */}
          <div className="relative flex-1 min-w-0">
            <input
              type="text"
              value={filters.query || ''}
              onChange={(e) => onFilterChange({ ...filters, query: e.target.value })}
              onKeyDown={(e) => e.key === 'Enter' && onSearch()}
              placeholder={SEARCH_PLACEHOLDER}
              aria-label="Search logs"
              enterKeyHint="search"
              data-active={active.query}
              className={`w-full bg-dark-900 border ${active.query ? ACTIVE_BORDER : IDLE_BORDER} rounded px-3 py-1.5 pl-8 ${filters.query ? 'pr-14' : 'pr-8'} text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono`}
            />
            <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-2" />
            {/* Search syntax help, left of the clear button. The wrapper is not positioned, so the
                popover is placed against the input (full input width on phones), not the icon. */}
            <div ref={helpRef} onBlur={handleHelpBlur}>
              <button
                ref={helpButtonRef}
                type="button"
                onClick={() => setIsHelpOpen((prev) => !prev)}
                className={`absolute top-1.5 ${filters.query ? 'right-7' : 'right-2'} block transition cursor-pointer ${isHelpOpen ? 'text-accent-400' : 'text-slate-500 hover:text-slate-300'}`}
                title="Search syntax"
                aria-label="Search syntax help"
                aria-expanded={isHelpOpen}
                aria-controls="search-syntax-help"
              >
                <CircleHelp className="w-3.5 h-3.5" />
              </button>
              {isHelpOpen && (
                <div
                  id="search-syntax-help"
                  role="dialog"
                  aria-label="Search syntax"
                  tabIndex={-1}
                  className="absolute inset-x-0 md:left-auto md:w-80 top-full mt-2 bg-dark-900 border border-dark-700 rounded-md shadow-xl p-3 z-50 font-sans text-xs select-text focus:outline-hidden"
                >
                  <p className="font-semibold text-slate-200 mb-2">Search syntax</p>
                  <ul className="space-y-1.5">
                    {SEARCH_SYNTAX_HELP.map((item) => (
                      <li key={item.example} className="flex flex-col gap-0.5">
                        <code className="font-mono text-accent-300">{item.example}</code>
                        <span className="text-slate-400 leading-snug">{item.text}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            {filters.query && (
              <button
                onClick={() => onFilterChange({ ...filters, query: '' })}
                className="absolute right-2 top-1.5 text-slate-500 hover:text-slate-300"
                title="Clear search query"
                aria-label="Clear search query"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Search now (filters already apply as they change; Enter does the same) */}
          <button
            type="button"
            onClick={() => onSearch()}
            title="Search now (Enter)"
            aria-label="Search now"
            className="flex items-center px-2.5 py-1.5 rounded bg-dark-900 border border-dark-700 text-slate-300 hover:text-white hover:border-slate-600 transition shrink-0 cursor-pointer"
          >
            <Search className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Second line on phones: filters drawer, match count and reset, then the toolbar the
            caller passes. With a toolbar the line is a compact band and the buttons show icons only. */}
        <div
          data-testid="search-bar-mobile-line"
          className={
            mobileToolbar
              ? '-mx-2.5 -mb-2.5 px-3 py-1.5 bg-dark-900 border-t border-dark-700 flex flex-nowrap items-center gap-2 relative overflow-hidden md:contents'
              : 'flex items-center gap-2 md:contents'
          }
        >
          {/* Mobile Filters Drawer Trigger Button */}
          <button
            onClick={() => setIsMobileDrawerOpen(true)}
            className={`md:hidden flex items-center gap-1.5 px-2.5 ${mobileToolbar ? 'py-1 bg-dark-800' : 'py-1.5 bg-dark-900'} rounded border border-dark-700 text-slate-300 hover:text-white shrink-0 cursor-pointer`}
            title="Open filters drawer"
            aria-label="Open filters drawer"
          >
            <SlidersHorizontal className="w-3.5 h-3.5 text-accent-400" />
            {!mobileToolbar && <span>Filters</span>}
            {activeFilterCount > 0 && (
              <span className="bg-accent-700 text-white text-[10px] font-mono px-1.5 py-0.2 rounded-full font-bold leading-none">
                {activeFilterCount}
              </span>
            )}
          </button>

          {/* Capped or Matched Count Indicator */}
          {hasActiveFilters && totalCount !== undefined && totalCount !== null && (
            <span
              className="text-slate-400 font-mono text-[11px] shrink-0"
              data-testid="search-bar-total-count"
              title={totalCapped ? 'Results capped at 1,000+ records' : `${totalCount.toLocaleString()} logs matched`}
            >
              {totalCapped ? '1,000+ logs' : `${totalCount.toLocaleString()} log${totalCount === 1 ? '' : 's'}`}
            </span>
          )}

          {/* Conditional Prominent Reset Button with Active Filter Count Indicator */}
          {hasActiveFilters && (
            <button
              onClick={handleResetFilters}
              title="Reset all active filters"
              aria-label="Reset all active filters"
              className={`bg-amber-950/70 hover:bg-amber-900/90 text-amber-300 border border-amber-600/70 px-2.5 ${mobileToolbar ? 'py-1 md:py-1.5' : 'py-1.5 ml-auto md:ml-0'} rounded flex items-center gap-1.5 transition shadow-xs font-medium cursor-pointer animate-in fade-in duration-150 shrink-0`}
            >
              <RotateCcw className="w-3.5 h-3.5 text-amber-400" />
              <span className={mobileToolbar ? 'hidden md:inline' : ''}>Reset</span>
              <span className="bg-amber-500/30 text-amber-200 border border-amber-500/50 text-[10px] font-mono px-1.5 py-0.5 rounded-full font-bold leading-none">
                {activeFilterCount}
              </span>
            </button>
          )}
          {mobileToolbar && <div className="md:hidden flex-1 min-w-0 flex items-center gap-2">{mobileToolbar}</div>}
        </div>
      </div>

      {/* Second Row: Desktop Cohesive Filter Dimension Row (hidden on mobile) */}
      <div className="hidden md:flex flex-wrap items-center gap-2 pt-1 border-t border-dark-800">
        {/* Host / IP Multi-Select */}
        <MultiSelectDropdown
          label="Host / IP"
          options={availableSources}
          selected={activeSources}
          onChange={(newSources) => {
            onFilterChange({
              ...filters,
              sources: newSources,
              source: newSources.length === 1 ? newSources[0] : (newSources.length > 1 ? newSources.join(',') : undefined),
            });
          }}
          placeholder="All Hosts"
          note={facetsTruncated ? facetsNote('hosts', facetsLimit) : undefined}
        />

        {/* App / Container Multi-Select */}
        <MultiSelectDropdown
          label="App / Container"
          options={availableApps}
          selected={activeApps}
          onChange={(newApps) => {
            onFilterChange({
              ...filters,
              apps: newApps,
              app_name: newApps.length === 1 ? newApps[0] : (newApps.length > 1 ? newApps.join(',') : undefined),
            });
          }}
          placeholder="All Apps"
          note={facetsTruncated ? facetsNote('apps', facetsLimit) : undefined}
        />

        {/* Severity Filter Dropdown */}
        <div
          data-active={active.severity}
          className={`flex items-center gap-1.5 bg-dark-900 border ${active.severity ? `${ACTIVE_BORDER} ${ACTIVE_BOX_TEXT}` : IDLE_BORDER} rounded px-2 py-1`}
        >
          <Filter className="w-3 h-3 text-slate-400" />
          <label htmlFor={`${fieldId}-severity`} className="text-slate-400 text-[11px] font-medium">Severity:</label>
          <select
            id={`${fieldId}-severity`}
            value={filters.severity_max !== undefined ? filters.severity_max : ''}
            onChange={(e) => {
              const val = e.target.value === '' ? undefined : parseInt(e.target.value, 10);
              onFilterChange({ ...filters, severity_max: val });
            }}
            title={
              filters.severity_max !== undefined && filters.severity_max !== null
                ? moreSevereLabel(filters.severity_max)
                : 'Choose a level to show it and everything more severe'
            }
            className={`bg-transparent text-[11px] focus:outline-hidden font-mono ${active.severity ? ACTIVE_VALUE : 'text-slate-200'}`}
          >
            <option value="" className="bg-dark-900 text-slate-200">All Severities</option>
            {SEVERITIES.map((s) => (
              <option key={s.value} value={s.value} className="bg-dark-900 text-slate-200">
                {compactMoreSevereLabel(s.value)}
              </option>
            ))}
          </select>
          {active.severity && (
            <button
              type="button"
              onClick={handleClearSeverity}
              className={`${CLEAR_BUTTON} p-0.5`}
              title="Clear severity filter"
              aria-label="Clear severity filter"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </div>

        {/* Time Preset Selector */}
        <div
          data-active={active.time}
          className={`flex items-center gap-1.5 bg-dark-900 border ${active.time ? `${ACTIVE_BORDER} ${ACTIVE_BOX_TEXT}` : IDLE_BORDER} rounded px-2 py-1`}
        >
          <Clock className="w-3 h-3 text-slate-400" />
          <label htmlFor={`${fieldId}-time`} className="text-slate-400 text-[11px] font-medium">Time:</label>
          <select
            id={`${fieldId}-time`}
            value={timePreset}
            onChange={(e) => handleTimePresetChange(e.target.value)}
            className={`bg-transparent text-[11px] focus:outline-hidden font-mono ${active.time ? ACTIVE_VALUE : 'text-slate-200'}`}
          >
            <option value="all" className="bg-dark-900 text-slate-200">All Time</option>
            <option value="15m" className="bg-dark-900 text-slate-200">Last 15m</option>
            <option value="1h" className="bg-dark-900 text-slate-200">Last 1h</option>
            <option value="6h" className="bg-dark-900 text-slate-200">Last 6h</option>
            <option value="24h" className="bg-dark-900 text-slate-200">Last 24h</option>
            <option value="7d" className="bg-dark-900 text-slate-200">Last 7d</option>
            <option value="since_midnight" className="bg-dark-900 text-slate-200">Today</option>
            <option value="custom" className="bg-dark-900 text-slate-200">Custom...</option>
          </select>
          {active.time && (
            <button
              type="button"
              onClick={handleClearTime}
              className={`${CLEAR_BUTTON} p-0.5`}
              title="Clear time filter"
              aria-label="Clear time filter"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </div>

        {/* Custom Datetime Pickers */}
        {showCustomTime && (
          <div className={`flex items-center gap-2 bg-dark-900 px-2 py-1 rounded border ${active.time ? ACTIVE_BORDER : IDLE_BORDER} w-full max-w-full md:w-auto`}>
            <div className={`flex items-center gap-1 min-w-0 flex-1 bg-dark-950 border ${filters.from ? ACTIVE_BORDER : IDLE_BORDER} rounded px-1.5 py-0.5 focus-within:border-accent-500`}>
              <label htmlFor={`${fieldId}-from`} className="text-slate-400 text-[11px] shrink-0">From:</label>
              <input
                id={`${fieldId}-from`}
                type="datetime-local"
                value={toLocalDatetimeInputString(filters.from, timeZone)}
                onChange={(e) =>
                  onFilterChange({
                    ...filters,
                    from: fromLocalDatetimeInputString(e.target.value, timeZone),
                  })
                }
                className="block w-full min-w-0 bg-transparent border-0 p-0 text-[11px] text-slate-200 font-mono focus:outline-hidden"
              />
            </div>
            <div className={`flex items-center gap-1 min-w-0 flex-1 bg-dark-950 border ${filters.to ? ACTIVE_BORDER : IDLE_BORDER} rounded px-1.5 py-0.5 focus-within:border-accent-500`}>
              <label htmlFor={`${fieldId}-to`} className="text-slate-400 text-[11px] shrink-0">To:</label>
              <input
                id={`${fieldId}-to`}
                type="datetime-local"
                value={toLocalDatetimeInputString(filters.to, timeZone)}
                onChange={(e) =>
                  onFilterChange({
                    ...filters,
                    to: fromLocalDatetimeInputString(e.target.value, timeZone),
                  })
                }
                className="block w-full min-w-0 bg-transparent border-0 p-0 text-[11px] text-slate-200 font-mono focus:outline-hidden"
              />
            </div>
            <span className="text-slate-400 text-[11px] font-mono shrink min-w-0 truncate" title="From and To are times in this zone">
              {timeInputZoneLabel(timeZone)}
            </span>
          </div>
        )}

        {viewsError && (
          <span role="alert" className="text-red-400 text-[11px]">
            {viewsError}
          </span>
        )}

        {/* Desktop Saved Views Dropdown */}
        <SavedViewsMenu
          variant="desktop"
          views={savedViews}
          isLoading={isViewsLoading}
          activeViewId={activeView?.id ?? null}
          onRenameView={handleRenameView}
          onApplyView={(params) => {
            if (onApplySavedView) onApplySavedView(params);
            else {
              onFilterChange(params);
              onSearch();
            }
          }}
          onTogglePin={handleTogglePin}
          onDeleteView={handleDeleteSavedView}
          onOpenSaveModal={() => setIsSaveModalOpen(true)}
        />
      </div>

      {/* Mobile Filter SlideOver Drawer */}
      <SlideOver
        isOpen={isMobileDrawerOpen}
        onClose={() => setIsMobileDrawerOpen(false)}
        title="Filter Logs"
        width="max-w-md"
      >
        <div className="space-y-4 text-xs font-sans">
          {/* Mobile Saved Views Section */}
          {viewsError && (
            <p role="alert" className="text-red-400 text-[11px]">
              {viewsError}
            </p>
          )}
          <SavedViewsMenu
            variant="mobile"
            views={savedViews}
            isLoading={isViewsLoading}
            activeViewId={activeView?.id ?? null}
            onRenameView={handleRenameView}
            onApplyView={(params) => {
              if (onApplySavedView) onApplySavedView(params);
              else {
                onFilterChange(params);
                onSearch();
              }
              setIsMobileDrawerOpen(false);
            }}
            onTogglePin={handleTogglePin}
            onDeleteView={handleDeleteSavedView}
            onOpenSaveModal={() => setIsSaveModalOpen(true)}
          />

          {/* Host / IP Filter */}
          <div>
            <label className="block text-slate-400 font-medium mb-1.5">Host / IP</label>
            <MultiSelectDropdown
              label="Host / IP"
              options={availableSources}
              selected={activeSources}
              onChange={(newSources) => {
                onFilterChange({
                  ...filters,
                  sources: newSources,
                  source: newSources.length === 1 ? newSources[0] : (newSources.length > 1 ? newSources.join(',') : undefined),
                });
              }}
              placeholder="All Hosts"
              note={facetsTruncated ? facetsNote('hosts', facetsLimit) : undefined}
            />
          </div>

          {/* App / Container Filter */}
          <div>
            <label className="block text-slate-400 font-medium mb-1.5">App / Container</label>
            <MultiSelectDropdown
              label="App / Container"
              options={availableApps}
              selected={activeApps}
              onChange={(newApps) => {
                onFilterChange({
                  ...filters,
                  apps: newApps,
                  app_name: newApps.length === 1 ? newApps[0] : (newApps.length > 1 ? newApps.join(',') : undefined),
                });
              }}
              placeholder="All Apps"
              note={facetsTruncated ? facetsNote('apps', facetsLimit) : undefined}
            />
          </div>

          {/* Severity Filter */}
          <div>
            <label htmlFor={`${fieldId}-severity-panel`} className="block text-slate-400 font-medium mb-1.5">Severity</label>
            <div data-active={active.severity} className={`flex items-center gap-2 ${active.severity ? ACTIVE_BOX_TEXT : ''}`}>
              <select
                id={`${fieldId}-severity-panel`}
                value={filters.severity_max !== undefined ? filters.severity_max : ''}
                onChange={(e) => {
                  const val = e.target.value === '' ? undefined : parseInt(e.target.value, 10);
                  onFilterChange({ ...filters, severity_max: val });
                }}
                className={`w-full min-w-0 flex-1 bg-dark-950 border ${active.severity ? `${ACTIVE_BORDER} ${ACTIVE_VALUE}` : `${IDLE_BORDER} text-slate-200`} rounded px-3 py-2 text-xs focus:outline-hidden font-mono`}
              >
                <option value="" className="text-slate-200">All Severities</option>
                {SEVERITIES.map((s) => (
                  <option key={s.value} value={s.value} className="text-slate-200">
                    {moreSevereLabel(s.value)}
                  </option>
                ))}
              </select>
              {active.severity && (
                <button
                  type="button"
                  onClick={handleClearSeverity}
                  className={`${CLEAR_BUTTON} p-1.5`}
                  title="Clear severity filter"
                  aria-label="Clear severity filter"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>

          {/* Time Preset */}
          <div>
            <label htmlFor={`${fieldId}-time-panel`} className="block text-slate-400 font-medium mb-1.5">Time Range</label>
            <div data-active={active.time} className={`flex items-center gap-2 ${active.time ? ACTIVE_BOX_TEXT : ''}`}>
              <select
                id={`${fieldId}-time-panel`}
                value={timePreset}
                onChange={(e) => handleTimePresetChange(e.target.value)}
                className={`w-full min-w-0 flex-1 bg-dark-950 border ${active.time ? `${ACTIVE_BORDER} ${ACTIVE_VALUE}` : `${IDLE_BORDER} text-slate-200`} rounded px-3 py-2 text-xs focus:outline-hidden font-mono`}
              >
                <option value="all" className="text-slate-200">All Time</option>
                <option value="15m" className="text-slate-200">Last 15m</option>
                <option value="1h" className="text-slate-200">Last 1h</option>
                <option value="6h" className="text-slate-200">Last 6h</option>
                <option value="24h" className="text-slate-200">Last 24h</option>
                <option value="7d" className="text-slate-200">Last 7d</option>
                <option value="since_midnight" className="text-slate-200">Today</option>
                <option value="custom" className="text-slate-200">Custom Range...</option>
              </select>
              {active.time && (
                <button
                  type="button"
                  onClick={handleClearTime}
                  className={`${CLEAR_BUTTON} p-1.5`}
                  title="Clear time filter"
                  aria-label="Clear time filter"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>

          {/* Custom Time */}
          {showCustomTime && (
            <div className="w-full space-y-2 p-2.5 bg-dark-950 rounded border border-dark-700">
              <div>
                <label htmlFor={`${fieldId}-from-panel`} className="block text-slate-400 text-[11px] mb-1">From:</label>
                <div className={`w-full bg-dark-900 border ${filters.from ? ACTIVE_BORDER : IDLE_BORDER} rounded px-2 py-1.5 focus-within:border-accent-500`}>
                  <input
                    id={`${fieldId}-from-panel`}
                    type="datetime-local"
                    value={toLocalDatetimeInputString(filters.from, timeZone)}
                    onChange={(e) =>
                      onFilterChange({
                        ...filters,
                        from: fromLocalDatetimeInputString(e.target.value, timeZone),
                      })
                    }
                    className="block w-full bg-transparent border-0 p-0 text-xs text-slate-200 font-mono focus:outline-hidden"
                  />
                </div>
              </div>
              <div>
                <label htmlFor={`${fieldId}-to-panel`} className="block text-slate-400 text-[11px] mb-1">To:</label>
                <div className={`w-full bg-dark-900 border ${filters.to ? ACTIVE_BORDER : IDLE_BORDER} rounded px-2 py-1.5 focus-within:border-accent-500`}>
                  <input
                    id={`${fieldId}-to-panel`}
                    type="datetime-local"
                    value={toLocalDatetimeInputString(filters.to, timeZone)}
                    onChange={(e) =>
                      onFilterChange({
                        ...filters,
                        to: fromLocalDatetimeInputString(e.target.value, timeZone),
                      })
                    }
                    className="block w-full bg-transparent border-0 p-0 text-xs text-slate-200 font-mono focus:outline-hidden"
                  />
                </div>
              </div>
              <p className="text-slate-400 text-[11px]">
                Times in <span className="font-mono">{timeInputZoneLabel(timeZone)}</span>
              </p>
            </div>
          )}

          {/* Actions */}
          <div className="pt-4 border-t border-dark-800 flex items-center gap-2">
            <button
              onClick={() => {
                setIsMobileDrawerOpen(false);
                onSearch();
              }}
              className="flex-1 bg-accent-700 hover:bg-accent-600 text-white font-medium py-2 rounded text-center transition"
            >
              Done
            </button>
            {hasActiveFilters && (
              <button
                onClick={() => {
                  handleResetFilters();
                  setIsMobileDrawerOpen(false);
                }}
                className="px-3 py-2 bg-dark-800 hover:bg-dark-700 text-slate-300 rounded transition font-medium"
              >
                Reset
              </button>
            )}
          </div>
        </div>
      </SlideOver>

      {/* Save View Modal */}
      <SaveViewModal
        isOpen={isSaveModalOpen}
        onClose={() => setIsSaveModalOpen(false)}
        currentFilters={filters}
        onSave={handleSaveViewSubmit}
        existingViews={savedViews}
      />
    </div>
  );
});

LogSearchBar.displayName = 'LogSearchBar';

export default LogSearchBar;

