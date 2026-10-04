import React, { useState, useMemo, useEffect, useCallback } from 'react';
import { Search, RotateCcw, Filter, Clock, X, SlidersHorizontal } from 'lucide-react';
import { LogFilterParams, SavedView } from '../../types.ts';
import { MultiSelectDropdown } from '../common/MultiSelectDropdown.tsx';
import { SlideOver } from '../common/SlideOver.tsx';
import { toLocalDatetimeInputString, fromLocalDatetimeInputString } from '../../utils/formatters.ts';
import { fetchSavedViews, createSavedView, updateSavedView, deleteSavedView } from '../../api/savedViews.ts';
import { SavedViewsMenu, SaveViewModal } from './SavedViewsMenu.tsx';

interface LogSearchBarProps {
  filters: LogFilterParams;
  onFilterChange: (filters: LogFilterParams) => void;
  onSearch: () => void;
  onReset: () => void;
  availableSources?: string[];
  availableApps?: string[];
  onApplySavedView?: (filters: LogFilterParams) => void;
  totalCount?: number;
  totalCapped?: boolean;
}

export const LogSearchBar: React.FC<LogSearchBarProps> = React.memo(({
  filters,
  onFilterChange,
  onSearch,
  onReset,
  availableSources = [],
  availableApps = [],
  onApplySavedView,
  totalCount,
  totalCapped = false,
}) => {
  const [timePreset, setTimePreset] = useState<string>('all');
  const [showCustomTime, setShowCustomTime] = useState<boolean>(false);
  const [isMobileDrawerOpen, setIsMobileDrawerOpen] = useState<boolean>(false);
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [isViewsLoading, setIsViewsLoading] = useState<boolean>(false);
  const [isSaveModalOpen, setIsSaveModalOpen] = useState<boolean>(false);

  const loadSavedViews = useCallback(async () => {
    try {
      setIsViewsLoading(true);
      const data = await fetchSavedViews();
      setSavedViews(data);
    } catch {
      // Non-critical background fetch
    } finally {
      setIsViewsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSavedViews();
  }, [loadSavedViews]);

  const handleTogglePin = useCallback(async (e: React.MouseEvent, view: SavedView) => {
    e.stopPropagation();
    try {
      const updated = await updateSavedView(view.id, { is_pinned: !view.is_pinned });
      setSavedViews((prev) =>
        prev
          .map((v) => (v.id === view.id ? updated : v))
          .sort((a, b) => (b.is_pinned ? 1 : 0) - (a.is_pinned ? 1 : 0) || a.name.localeCompare(b.name))
      );
    } catch {
      // Ignored
    }
  }, []);

  const handleDeleteSavedView = useCallback(async (e: React.MouseEvent, viewId: number) => {
    e.stopPropagation();
    try {
      await deleteSavedView(viewId);
      setSavedViews((prev) => prev.filter((v) => v.id !== viewId));
    } catch {
      // Ignored
    }
  }, []);

  const handleSaveViewSubmit = useCallback(async (name: string, isPinned: boolean) => {
    const cleanParams: Record<string, any> = {};
    if (filters.query?.trim()) cleanParams.query = filters.query.trim();
    if (filters.severity_max !== undefined && filters.severity_max !== null) {
      cleanParams.severity_max = filters.severity_max;
    }
    if (filters.sources && filters.sources.length > 0) {
      cleanParams.sources = filters.sources;
    } else if (filters.source) {
      cleanParams.source = filters.source;
    }
    if (filters.apps && filters.apps.length > 0) {
      cleanParams.apps = filters.apps;
    } else if (filters.app_name) {
      cleanParams.app_name = filters.app_name;
    }
    if (filters.from) cleanParams.from = filters.from;
    if (filters.to) cleanParams.to = filters.to;

    const created = await createSavedView({
      name,
      query_params: cleanParams,
      is_pinned: isPinned,
    });

    setSavedViews((prev) =>
      [...prev, created].sort(
        (a, b) => (b.is_pinned ? 1 : 0) - (a.is_pinned ? 1 : 0) || a.name.localeCompare(b.name)
      )
    );
  }, [filters]);

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

  // Count active filters to conditionally show Reset button with badge
  const activeFilterCount = useMemo(() => {
    let count = 0;
    if (filters.query && filters.query.trim()) count += 1;
    if (filters.severity_max !== undefined && filters.severity_max !== null) count += 1;
    if (activeSources.length > 0) count += activeSources.length;
    if (activeApps.length > 0) count += activeApps.length;
    if (timePreset !== 'all' || filters.from || filters.to) count += 1;
    return count;
  }, [filters.query, filters.severity_max, filters.from, filters.to, activeSources, activeApps, timePreset]);

  const hasActiveFilters = activeFilterCount > 0;

  const handleTimePresetChange = useCallback((preset: string) => {
    setTimePreset(preset);
    if (preset === 'custom') {
      setShowCustomTime(true);
      return;
    }

    setShowCustomTime(false);
    if (preset === 'all') {
      onFilterChange({ ...filters, from: undefined, to: undefined });
      return;
    }

    const now = new Date();
    let fromDate = new Date();
    if (preset === '15m') fromDate = new Date(now.getTime() - 15 * 60 * 1000);
    else if (preset === '1h') fromDate = new Date(now.getTime() - 60 * 60 * 1000);
    else if (preset === '6h') fromDate = new Date(now.getTime() - 6 * 60 * 60 * 1000);
    else if (preset === '24h') fromDate = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    else if (preset === '7d') fromDate = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);

    onFilterChange({
      ...filters,
      from: fromDate.toISOString(),
      to: undefined,
    });
  }, [filters, onFilterChange]);

  const handleResetFilters = useCallback(() => {
    setTimePreset('all');
    setShowCustomTime(false);
    onReset();
  }, [onReset]);

  return (
    <div className="bg-dark-950 border-b border-dark-700 p-2.5 flex flex-col gap-2 select-none text-xs shrink-0">
      {/* Top Row: Search Input & Primary Search Actions */}
      <div className="flex items-center gap-2">
        {/* FTS Search Input */}
        <div className="relative flex-1 min-w-0">
          <input
            type="text"
            value={filters.query || ''}
            onChange={(e) => onFilterChange({ ...filters, query: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && onSearch()}
            placeholder="Full-text search (FTS5 syntax: error, timeout, status:*)..."
            className="w-full bg-dark-900 border border-dark-700 rounded px-3 py-1.5 pl-8 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
          />
          <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-2" />
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

        {/* Mobile Filters Drawer Trigger Button */}
        <button
          onClick={() => setIsMobileDrawerOpen(true)}
          className="md:hidden flex items-center gap-1.5 px-2.5 py-1.5 rounded bg-dark-900 border border-dark-700 text-slate-300 hover:text-white shrink-0 cursor-pointer"
          title="Open filters drawer"
          aria-label="Open filters drawer"
        >
          <SlidersHorizontal className="w-3.5 h-3.5 text-accent-400" />
          <span>Filters</span>
          {activeFilterCount > 0 && (
            <span className="bg-accent-600 text-white text-[10px] font-mono px-1.5 py-0.2 rounded-full font-bold leading-none">
              {activeFilterCount}
            </span>
          )}
        </button>

        {/* Action Button: Filter */}
        <button
          onClick={onSearch}
          className="bg-accent-600 hover:bg-accent-500 text-white font-medium px-3 py-1.5 rounded flex items-center gap-1 transition shadow-xs cursor-pointer shrink-0"
        >
          <Search className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">Filter</span>
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
            className="bg-amber-950/70 hover:bg-amber-900/90 text-amber-300 border border-amber-600/70 px-2.5 py-1.5 rounded flex items-center gap-1.5 transition shadow-xs font-medium cursor-pointer animate-in fade-in duration-150 shrink-0"
          >
            <RotateCcw className="w-3.5 h-3.5 text-amber-400" />
            <span className="hidden sm:inline">Reset</span>
            <span className="bg-amber-500/30 text-amber-200 border border-amber-500/50 text-[10px] font-mono px-1.5 py-0.5 rounded-full font-bold leading-none">
              {activeFilterCount}
            </span>
          </button>
        )}
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
        />

        {/* Severity Filter Dropdown */}
        <div className="flex items-center gap-1.5 bg-dark-900 border border-dark-700 rounded px-2 py-1">
          <Filter className="w-3 h-3 text-slate-400" />
          <span className="text-slate-400 text-[11px] font-medium">Severity:</span>
          <select
            value={filters.severity_max !== undefined ? filters.severity_max : ''}
            onChange={(e) => {
              const val = e.target.value === '' ? undefined : parseInt(e.target.value, 10);
              onFilterChange({ ...filters, severity_max: val });
            }}
            className="bg-transparent text-[11px] text-slate-200 focus:outline-hidden font-mono"
          >
            <option value="" className="bg-dark-900">All Severities</option>
            <option value="0" className="bg-dark-900">≤ Emerg (0)</option>
            <option value="1" className="bg-dark-900">≤ Alert (1)</option>
            <option value="2" className="bg-dark-900">≤ Crit (2)</option>
            <option value="3" className="bg-dark-900">≤ Error (3)</option>
            <option value="4" className="bg-dark-900">≤ Warn (4)</option>
            <option value="5" className="bg-dark-900">≤ Notice (5)</option>
            <option value="6" className="bg-dark-900">≤ Info (6)</option>
            <option value="7" className="bg-dark-900">≤ Debug (7)</option>
          </select>
        </div>

        {/* Time Preset Selector */}
        <div className="flex items-center gap-1.5 bg-dark-900 border border-dark-700 rounded px-2 py-1">
          <Clock className="w-3 h-3 text-slate-400" />
          <span className="text-slate-400 text-[11px] font-medium">Time:</span>
          <select
            value={timePreset}
            onChange={(e) => handleTimePresetChange(e.target.value)}
            className="bg-transparent text-[11px] text-slate-200 focus:outline-hidden font-mono"
          >
            <option value="all" className="bg-dark-900">All Time</option>
            <option value="15m" className="bg-dark-900">Last 15m</option>
            <option value="1h" className="bg-dark-900">Last 1h</option>
            <option value="6h" className="bg-dark-900">Last 6h</option>
            <option value="24h" className="bg-dark-900">Last 24h</option>
            <option value="7d" className="bg-dark-900">Last 7d</option>
            <option value="custom" className="bg-dark-900">Custom...</option>
          </select>
        </div>

        {/* Custom Datetime Pickers */}
        {showCustomTime && (
          <div className="flex items-center gap-2 bg-dark-900 px-2 py-1 rounded border border-dark-700 w-full max-w-full md:w-auto">
            <div className="flex items-center gap-1 min-w-0 flex-1 bg-dark-950 border border-dark-700 rounded px-1.5 py-0.5 focus-within:border-accent-500">
              <span className="text-slate-400 text-[11px] shrink-0">From:</span>
              <input
                type="datetime-local"
                value={toLocalDatetimeInputString(filters.from)}
                onChange={(e) =>
                  onFilterChange({
                    ...filters,
                    from: fromLocalDatetimeInputString(e.target.value),
                  })
                }
                className="block w-full min-w-0 bg-transparent border-0 p-0 text-[11px] text-slate-200 font-mono focus:outline-hidden"
              />
            </div>
            <div className="flex items-center gap-1 min-w-0 flex-1 bg-dark-950 border border-dark-700 rounded px-1.5 py-0.5 focus-within:border-accent-500">
              <span className="text-slate-400 text-[11px] shrink-0">To:</span>
              <input
                type="datetime-local"
                value={toLocalDatetimeInputString(filters.to)}
                onChange={(e) =>
                  onFilterChange({
                    ...filters,
                    to: fromLocalDatetimeInputString(e.target.value),
                  })
                }
                className="block w-full min-w-0 bg-transparent border-0 p-0 text-[11px] text-slate-200 font-mono focus:outline-hidden"
              />
            </div>
          </div>
        )}

        {/* Desktop Saved Views Dropdown */}
        <SavedViewsMenu
          variant="desktop"
          views={savedViews}
          isLoading={isViewsLoading}
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
          <SavedViewsMenu
            variant="mobile"
            views={savedViews}
            isLoading={isViewsLoading}
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
            />
          </div>

          {/* Severity Filter */}
          <div>
            <label className="block text-slate-400 font-medium mb-1.5">Maximum Severity</label>
            <select
              value={filters.severity_max !== undefined ? filters.severity_max : ''}
              onChange={(e) => {
                const val = e.target.value === '' ? undefined : parseInt(e.target.value, 10);
                onFilterChange({ ...filters, severity_max: val });
              }}
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-hidden font-mono"
            >
              <option value="">All Severities</option>
              <option value="0">≤ Emerg (0)</option>
              <option value="1">≤ Alert (1)</option>
              <option value="2">≤ Crit (2)</option>
              <option value="3">≤ Error (3)</option>
              <option value="4">≤ Warn (4)</option>
              <option value="5">≤ Notice (5)</option>
              <option value="6">≤ Info (6)</option>
              <option value="7">≤ Debug (7)</option>
            </select>
          </div>

          {/* Time Preset */}
          <div>
            <label className="block text-slate-400 font-medium mb-1.5">Time Range</label>
            <select
              value={timePreset}
              onChange={(e) => handleTimePresetChange(e.target.value)}
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-200 focus:outline-hidden font-mono"
            >
              <option value="all">All Time</option>
              <option value="15m">Last 15m</option>
              <option value="1h">Last 1h</option>
              <option value="6h">Last 6h</option>
              <option value="24h">Last 24h</option>
              <option value="7d">Last 7d</option>
              <option value="custom">Custom Range...</option>
            </select>
          </div>

          {/* Custom Time */}
          {showCustomTime && (
            <div className="w-full space-y-2 p-2.5 bg-dark-950 rounded border border-dark-700">
              <div>
                <label className="block text-slate-400 text-[11px] mb-1">From:</label>
                <div className="w-full bg-dark-900 border border-dark-700 rounded px-2 py-1.5 focus-within:border-accent-500">
                  <input
                    type="datetime-local"
                    value={toLocalDatetimeInputString(filters.from)}
                    onChange={(e) =>
                      onFilterChange({
                        ...filters,
                        from: fromLocalDatetimeInputString(e.target.value),
                      })
                    }
                    className="block w-full bg-transparent border-0 p-0 text-xs text-slate-200 font-mono focus:outline-hidden"
                  />
                </div>
              </div>
              <div>
                <label className="block text-slate-400 text-[11px] mb-1">To:</label>
                <div className="w-full bg-dark-900 border border-dark-700 rounded px-2 py-1.5 focus-within:border-accent-500">
                  <input
                    type="datetime-local"
                    value={toLocalDatetimeInputString(filters.to)}
                    onChange={(e) =>
                      onFilterChange({
                        ...filters,
                        to: fromLocalDatetimeInputString(e.target.value),
                      })
                    }
                    className="block w-full bg-transparent border-0 p-0 text-xs text-slate-200 font-mono focus:outline-hidden"
                  />
                </div>
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="pt-4 border-t border-dark-800 flex items-center gap-2">
            <button
              onClick={() => {
                setIsMobileDrawerOpen(false);
                onSearch();
              }}
              className="flex-1 bg-accent-600 hover:bg-accent-500 text-white font-medium py-2 rounded text-center transition"
            >
              Apply Filters
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
      />
    </div>
  );
});

LogSearchBar.displayName = 'LogSearchBar';

export default LogSearchBar;

