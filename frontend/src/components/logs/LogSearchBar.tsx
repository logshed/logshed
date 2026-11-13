import React, { useState, useMemo, useEffect, useCallback, useRef, useId } from 'react';
import { Search, RotateCcw, X, SlidersHorizontal, CircleHelp } from 'lucide-react';
import { LogFilterParams, SavedView } from '../../types.ts';
import { MultiSelectDropdown } from '../common/MultiSelectDropdown.tsx';
import { SelectDropdown, SelectOption } from '../common/SelectDropdown.tsx';
import { SlideOver } from '../common/SlideOver.tsx';
import { SEVERITIES } from '../common/SeverityBadge.tsx';
import { moreSevereLabel, compactMoreSevereLabel } from '../../utils/severityLabels.ts';
import {
  toLocalDatetimeInputString,
  fromLocalDatetimeInputString,
  startOfTodayIso,
  timeInputZoneLabel,
} from '../../utils/formatters.ts';
import { hostFilterList, appFilterList, withHosts, withApps } from '../../utils/filters.ts';
import { SavedViewsMenu, SaveViewModal } from './SavedViewsMenu.tsx';
import { SavedViewsState, useSavedViews, findActiveView } from './useSavedViews.ts';
import { buttonClass } from '../common/Button.tsx';

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
  { example: 'io*', text: 'A word of one or two letters matches only that word; add * to match the start of longer ones.' },
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

// Severity choices: the toolbar shows the short names, the phone panel the full ones
const compactSeverityOptions: SelectOption[] = [
  { value: '', label: 'All Severities' },
  ...SEVERITIES.map((s) => ({ value: String(s.value), label: compactMoreSevereLabel(s.value) })),
];
const severityOptions: SelectOption[] = [
  { value: '', label: 'All Severities' },
  ...SEVERITIES.map((s) => ({ value: String(s.value), label: moreSevereLabel(s.value) })),
];

/** Time presets; the last one shows the From and To fields. */
const timeOptions = (customLabel: string): SelectOption[] => [
  { value: 'all', label: 'All Time' },
  { value: '15m', label: 'Last 15m' },
  { value: '1h', label: 'Last 1h' },
  { value: '6h', label: 'Last 6h' },
  { value: '24h', label: 'Last 24h' },
  { value: '7d', label: 'Last 7d' },
  { value: SINCE_MIDNIGHT, label: 'Today' },
  { value: 'custom', label: customLabel },
];

// The active border of the filter dropdowns (dropdownStyles.ts), reused for the search box and the
// custom time fields.
const ACTIVE_BORDER = 'border-accent-600/60';
const IDLE_BORDER = 'border-line';

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

  const activeSources = useMemo(() => hostFilterList(filters), [filters]);
  const activeApps = useMemo(() => appFilterList(filters), [filters]);

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

  const severityValue = filters.severity_max !== undefined && filters.severity_max !== null ? String(filters.severity_max) : '';
  const handleSeverityChange = (val: string) =>
    onFilterChange({ ...filters, severity_max: val === '' ? undefined : parseInt(val, 10) });
  const handleClearSeverity = () => onFilterChange({ ...filters, severity_max: undefined });
  const handleClearTime = () => handleTimePresetChange('all');

  const handleResetFilters = useCallback(() => {
    setCustomTimeRequested(false);
    onReset();
  }, [onReset]);

  return (
    <div className="bg-canvas border-b border-line p-2.5 flex flex-col gap-2 select-none text-xs shrink-0">
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
              className={`w-full bg-surface border ${active.query ? ACTIVE_BORDER : IDLE_BORDER} rounded-sm px-3 py-1.5 pl-8 ${filters.query ? 'pr-14' : 'pr-8'} text-xs text-fg-strong placeholder-fg-subtle focus:outline-hidden focus:border-accent-500 font-mono`}
            />
            {/* The icon and the two buttons are centred on the input, so they stay in line at any input height */}
            <Search className="w-3.5 h-3.5 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2" />
            {/* Search syntax help, left of the clear button. The wrapper is not positioned, so the
                popover is placed against the input (full input width on phones), not the icon. */}
            <div ref={helpRef} onBlur={handleHelpBlur}>
              <button
                ref={helpButtonRef}
                type="button"
                onClick={() => setIsHelpOpen((prev) => !prev)}
                className={`absolute top-1/2 -translate-y-1/2 leading-none ${filters.query ? 'right-7' : 'right-2'} block transition-colors cursor-pointer ${isHelpOpen ? 'text-accent-400' : 'text-fg-subtle hover:text-fg-soft'}`}
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
                  className="absolute inset-x-0 md:left-auto md:w-80 top-full mt-2 bg-surface border border-line rounded-md shadow-xl p-3 z-50 font-sans text-xs select-text focus:outline-hidden"
                >
                  <p className="font-semibold text-fg mb-2">Search syntax</p>
                  <ul className="space-y-1.5">
                    {SEARCH_SYNTAX_HELP.map((item) => (
                      <li key={item.example} className="flex flex-col gap-0.5">
                        <code className="font-mono text-accent-300">{item.example}</code>
                        <span className="text-fg-muted leading-snug">{item.text}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            {filters.query && (
              <button
                onClick={() => onFilterChange({ ...filters, query: '' })}
                className="absolute right-2 top-1/2 -translate-y-1/2 leading-none text-fg-subtle hover:text-fg-soft"
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
            className="flex items-center px-2.5 py-1.5 rounded-sm bg-surface border border-line text-fg-soft hover:text-fg-max hover:border-line-stronger transition shrink-0 cursor-pointer"
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
              ? '-mx-2.5 -mb-2.5 px-3 py-1.5 bg-surface border-t border-line flex flex-nowrap items-center gap-2 relative overflow-hidden md:contents'
              : 'flex items-center gap-2 md:contents'
          }
        >
          {/* Mobile Filters Drawer Trigger Button */}
          <button
            onClick={() => setIsMobileDrawerOpen(true)}
            className={`md:hidden flex items-center gap-1.5 px-2.5 ${mobileToolbar ? 'py-1 bg-raised' : 'py-1.5 bg-surface'} rounded-sm border border-line text-fg-soft hover:text-fg-max shrink-0 cursor-pointer`}
            title="Open filters drawer"
            aria-haspopup="dialog"
          >
            {/* Named by its text, count included ("Filters 3 active"), so the name matches what is shown */}
            <SlidersHorizontal className="w-3.5 h-3.5 text-accent-400" aria-hidden="true" />
            <span className={mobileToolbar ? 'sr-only' : ''}>Filters</span>
            {activeFilterCount > 0 && (
              <span className="bg-accent-700 text-on-fill text-[10px] font-mono px-1.5 py-0.2 rounded-full font-bold leading-none">
                {' '}
                {activeFilterCount}
                <span className="sr-only"> active</span>
              </span>
            )}
          </button>

          {/* Capped or Matched Count Indicator */}
          {hasActiveFilters && totalCount !== undefined && totalCount !== null && (
            <span
              className="text-fg-muted font-mono text-[11px] shrink-0"
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
              className={`bg-warning-950/70 hover:bg-warning-900/90 text-warning-300 border border-warning-600/70 px-2.5 ${mobileToolbar ? 'py-1 md:py-1.5' : 'py-1.5 ml-auto md:ml-0'} rounded-sm flex items-center gap-1.5 transition shadow-xs font-medium cursor-pointer duration-150 shrink-0`}
            >
              {/* Named by its text ("Reset 3 active filters"); on the phone toolbar the word is read, not shown */}
              <RotateCcw className="w-3.5 h-3.5 text-warning-400" aria-hidden="true" />
              <span className={mobileToolbar ? 'sr-only md:not-sr-only' : ''}>Reset</span>
              <span className="bg-warning-500/30 text-warning-200 border border-warning-500/50 text-[10px] font-mono px-1.5 py-0.5 rounded-full font-bold leading-none">
                {' '}
                {activeFilterCount}
                <span className="sr-only"> active filters</span>
              </span>
            </button>
          )}
          {mobileToolbar && <div className="md:hidden flex-1 min-w-0 flex items-center gap-2">{mobileToolbar}</div>}
        </div>
      </div>

      {/* Second Row: Desktop Cohesive Filter Dimension Row (hidden on mobile) */}
      <div className="hidden md:flex flex-wrap items-center gap-2 pt-1 border-t border-line-subtle">
        {/* Host / IP Multi-Select */}
        <MultiSelectDropdown
          label="Host / IP"
          options={availableSources}
          selected={activeSources}
          onChange={(newSources) => {
            onFilterChange(withHosts(filters, newSources));
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
            onFilterChange(withApps(filters, newApps));
          }}
          placeholder="All Apps"
          note={facetsTruncated ? facetsNote('apps', facetsLimit) : undefined}
        />

        {/* Severity Filter Dropdown */}
        <SelectDropdown
          label="Severity"
          options={compactSeverityOptions}
          value={severityValue}
          onChange={handleSeverityChange}
          active={active.severity}
          onClear={handleClearSeverity}
          clearLabel="Clear severity filter"
          title={
            filters.severity_max !== undefined && filters.severity_max !== null
              ? moreSevereLabel(filters.severity_max)
              : 'Choose a level to show it and everything more severe'
          }
        />

        {/* Time Preset Selector */}
        <SelectDropdown
          label="Time"
          options={timeOptions('Custom...')}
          value={timePreset}
          onChange={handleTimePresetChange}
          active={active.time}
          onClear={handleClearTime}
          clearLabel="Clear time filter"
        />

        {/* Custom Datetime Pickers */}
        {showCustomTime && (
          // From and To as two boxes of the dropdowns' height, so choosing Custom... does not make the row taller
          <div className="flex items-center gap-2 w-full max-w-full md:w-auto">
            <div className={`flex items-center gap-1 min-w-0 flex-1 h-[26px] bg-surface border ${filters.from ? ACTIVE_BORDER : IDLE_BORDER} rounded-sm px-2.5 focus-within:border-accent-500`}>
              <label htmlFor={`${fieldId}-from`} className="text-fg-muted text-[11px] shrink-0">From:</label>
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
                className="block w-full min-w-0 bg-transparent border-0 p-0 text-[11px] text-fg font-mono focus:outline-hidden"
              />
            </div>
            <div className={`flex items-center gap-1 min-w-0 flex-1 h-[26px] bg-surface border ${filters.to ? ACTIVE_BORDER : IDLE_BORDER} rounded-sm px-2.5 focus-within:border-accent-500`}>
              <label htmlFor={`${fieldId}-to`} className="text-fg-muted text-[11px] shrink-0">To:</label>
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
                className="block w-full min-w-0 bg-transparent border-0 p-0 text-[11px] text-fg font-mono focus:outline-hidden"
              />
            </div>
            <span className="text-fg-muted text-[11px] font-mono shrink min-w-0 truncate" title="From and To are times in this zone">
              {timeInputZoneLabel(timeZone)}
            </span>
          </div>
        )}

        {viewsError && (
          <span role="alert" className="text-danger-400 text-[11px]">
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
            <p role="alert" className="text-danger-400 text-[11px]">
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
            <label className="block text-fg-muted font-medium mb-1.5">Host / IP</label>
            <MultiSelectDropdown
              label="Host / IP"
              options={availableSources}
              selected={activeSources}
              onChange={(newSources) => {
                onFilterChange(withHosts(filters, newSources));
              }}
              placeholder="All Hosts"
              note={facetsTruncated ? facetsNote('hosts', facetsLimit) : undefined}
            />
          </div>

          {/* App / Container Filter */}
          <div>
            <label className="block text-fg-muted font-medium mb-1.5">App / Container</label>
            <MultiSelectDropdown
              label="App / Container"
              options={availableApps}
              selected={activeApps}
              onChange={(newApps) => {
                onFilterChange(withApps(filters, newApps));
              }}
              placeholder="All Apps"
              note={facetsTruncated ? facetsNote('apps', facetsLimit) : undefined}
            />
          </div>

          {/* Severity Filter */}
          <div>
            <label id={`${fieldId}-severity-panel-label`} htmlFor={`${fieldId}-severity-panel`} className="block text-fg-muted font-medium mb-1.5">Severity</label>
            <SelectDropdown
              variant="panel"
              id={`${fieldId}-severity-panel`}
              labelledBy={`${fieldId}-severity-panel-label`}
              label="Severity"
              options={severityOptions}
              value={severityValue}
              onChange={handleSeverityChange}
              active={active.severity}
              onClear={handleClearSeverity}
              clearLabel="Clear severity filter"
            />
          </div>

          {/* Time Preset */}
          <div>
            <label id={`${fieldId}-time-panel-label`} htmlFor={`${fieldId}-time-panel`} className="block text-fg-muted font-medium mb-1.5">Time Range</label>
            <SelectDropdown
              variant="panel"
              id={`${fieldId}-time-panel`}
              labelledBy={`${fieldId}-time-panel-label`}
              label="Time Range"
              options={timeOptions('Custom Range...')}
              value={timePreset}
              onChange={handleTimePresetChange}
              active={active.time}
              onClear={handleClearTime}
              clearLabel="Clear time filter"
            />
          </div>

          {/* Custom Time */}
          {showCustomTime && (
            <div className="w-full space-y-2 p-2.5 bg-canvas rounded-sm border border-line">
              <div>
                <label htmlFor={`${fieldId}-from-panel`} className="block text-fg-muted text-[11px] mb-1">From:</label>
                <div className={`w-full bg-surface border ${filters.from ? ACTIVE_BORDER : IDLE_BORDER} rounded-sm px-2 py-1.5 focus-within:border-accent-500`}>
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
                    className="block w-full bg-transparent border-0 p-0 text-xs text-fg font-mono focus:outline-hidden"
                  />
                </div>
              </div>
              <div>
                <label htmlFor={`${fieldId}-to-panel`} className="block text-fg-muted text-[11px] mb-1">To:</label>
                <div className={`w-full bg-surface border ${filters.to ? ACTIVE_BORDER : IDLE_BORDER} rounded-sm px-2 py-1.5 focus-within:border-accent-500`}>
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
                    className="block w-full bg-transparent border-0 p-0 text-xs text-fg font-mono focus:outline-hidden"
                  />
                </div>
              </div>
              <p className="text-fg-muted text-[11px]">
                Times in <span className="font-mono">{timeInputZoneLabel(timeZone)}</span>
              </p>
            </div>
          )}

          {/* Actions */}
          <div className="pt-4 border-t border-line-subtle flex items-center gap-2">
            <button
              onClick={() => {
                setIsMobileDrawerOpen(false);
                onSearch();
              }}
              className="flex-1 bg-accent-700 hover:bg-accent-600 text-on-fill font-medium py-2 rounded-sm text-center transition"
            >
              Done
            </button>
            {hasActiveFilters && (
              <button
                onClick={() => {
                  handleResetFilters();
                  setIsMobileDrawerOpen(false);
                }}
                className={buttonClass('secondary', 'md')}
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

