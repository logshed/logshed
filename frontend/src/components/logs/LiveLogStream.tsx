import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { CheckSquare, Square, ArrowUp, Loader2, AlertCircle } from 'lucide-react';
import { LogEntry, LogFacetsResponse, LogFilterParams } from '../../types.ts';
import { LogSearchBar } from './LogSearchBar.tsx';
import { useSavedViews, findActiveView } from './useSavedViews.ts';
import { LogDetailModal } from './LogDetailModal.tsx';
import { deleteLogs } from '../../api/logs.ts';
import { getAuthStatus } from '../../api/auth.ts';
import { fetchSettings, getCachedServerTimezone } from '../../api/settings.ts';
import { useMediaQuery } from '../../utils/hooks.ts';
import { cleanLogMessageForDisplay } from '../../utils/formatters.ts';
import { PullTouchHandlers } from '../../utils/usePullToRefresh.ts';
import { ProcessedLogEntry } from '../../utils/logEntry.ts';
import { CompiledSearchQuery, compileSearchQuery, matchesSearchQuery } from '../../utils/searchQuery.ts';
import {
  appFilterList,
  hasActiveLogFilters,
  hostFilterList,
  parseFiltersFromUrl,
  parseFocusLogIdFromUrl,
  removeFocusFromUrl,
  renameFilterHosts,
  syncFiltersToUrl,
  toggleListValue,
  withApps,
  withHostAppLists,
  withHosts,
} from '../../utils/filters.ts';
import {
  LogRow,
  logGridCols,
  logRowId,
  DESKTOP_LOG_ROW_HEIGHT,
  MOBILE_LOG_ROW_HEIGHT,
} from './LogRow.tsx';
import { setAlertRuleHandoff } from '../../utils/alertRuleHandoff.ts';
import { SCROLLBACK_BUFFER_SIZE, useLogBuffer } from './useLogBuffer.ts';
import { SELECT_ALL_LIMIT, useLogSelection } from './useLogSelection.ts';
import { SourcesChange, useSourcesConfig } from './useSourcesConfig.ts';
import { useLogFacets } from './useLogFacets.ts';
import { useQuickFilterChips } from './useQuickFilterChips.ts';
import { StreamToolbar } from './StreamToolbar.tsx';
import { SelectionActionBar } from './SelectionActionBar.tsx';
import { DeleteSelectedDialog } from './DeleteSelectedDialog.tsx';
import { ConsoleEmptyKind, ConsoleEmptyState } from './ConsoleEmptyState.tsx';

// Loaded on first use, not with the Console
const CreateDropRuleModal = React.lazy(() =>
  import('../alerts/CreateDropRuleModal.tsx').then((m) => ({ default: m.CreateDropRuleModal }))
);

interface LiveLogStreamProps {
  onDiagnoseAi: (selectedLogs: LogEntry[]) => void;
  /** Opens Settings on the Sources tab (used by the first-run card). */
  onOpenSourcesSettings?: () => void;
  onAddAlias?: (ip: string) => void;
  onEditDockerHost?: (endpointId: number) => void;
  onManageDeletedDockerHost?: (info: { name: string; ip: string }) => void;
  clearSelectionSignal?: number;
  pullTouchHandlers?: PullTouchHandlers;
}

// Pages of older logs loaded at most while looking for a focused log (500 logs each)
const MAX_FOCUS_PAGES = 10;

const EMPTY_SELECTED_LOGS: ProcessedLogEntry[] = [];

export const LiveLogStream: React.FC<LiveLogStreamProps> = ({
  onDiagnoseAi,
  onOpenSourcesSettings,
  onAddAlias,
  onEditDockerHost,
  onManageDeletedDockerHost,
  clearSelectionSignal,
  pullTouchHandlers,
}) => {
  // Host and app filters are lists only (utils/filters.ts); withHostAppLists converts any others
  const [filters, setFilters] = useState<LogFilterParams>(() => parseFiltersFromUrl());
  // The log an alert link points at: scrolled into view and highlighted once, cleared when the filters change
  const [focusLogId, setFocusLogId] = useState<number | null>(() => parseFocusLogIdFromUrl());
  const initialFiltersKeyRef = useRef<string>(JSON.stringify(filters));
  const focusScrolledRef = useRef<boolean>(false);
  const focusPagesLoadedRef = useRef<number>(0);
  const [activeLogDetail, setActiveLogDetail] = useState<LogEntry | null>(null);
  const [dropRuleTargetLog, setDropRuleTargetLog] = useState<LogEntry | null>(null);
  const [showDeleteSelectedConfirm, setShowDeleteSelectedConfirm] = useState<boolean>(false);
  const [streamDisconnected, setStreamDisconnected] = useState<boolean>(false);
  const [streamReconnectNonce, setStreamReconnectNonce] = useState<number>(0);
  const [serverTimezone, setServerTimezone] = useState<string>(() => getCachedServerTimezone() || '');
  // Off until settings load, so users who never turned AI on do not see AI buttons flash in
  const [aiEnabled, setAiEnabled] = useState<boolean>(false);
  // The listening port from settings, for the first-run card; undefined until settings load
  const [syslogPort, setSyslogPort] = useState<number | undefined>(undefined);
  // Whether the database holds any logs: null until the facets arrive, true once any log is seen.
  // It picks the empty-state text: "Waiting for new logs..." or the first-run card.
  const [databaseHasLogs, setDatabaseHasLogs] = useState<boolean | null>(null);
  // Set when the Quick Filter chips should be ranked again from the next list (new results, cleared buffer)
  const chipRerankRef = useRef<boolean>(true);

  useEffect(() => {
    fetchSettings()
      .then((s) => {
        if (s.server_timezone) setServerTimezone(s.server_timezone);
        setAiEnabled(s.ai_enabled ?? Boolean(s.has_ai_api_key));
        if (s.syslog_port) setSyslogPort(s.syslog_port);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    syncFiltersToUrl(filters);
  }, [filters]);

  useEffect(() => {
    if (focusLogId === null || JSON.stringify(filters) === initialFiltersKeyRef.current) return;
    setFocusLogId(null);
    removeFocusFromUrl();
  }, [filters, focusLogId]);

  const activeSources = useMemo(() => hostFilterList(filters), [filters]);
  const activeApps = useMemo(() => appFilterList(filters), [filters]);

  const isMobile = useMediaQuery('(max-width: 767px)');

  // The list as last rendered, shared by the buffer and the selection's Shift-click ranges
  const logsRef = useRef<ProcessedLogEntry[]>([]);
  const selection = useLogSelection(logsRef, clearSelectionSignal);
  const { shiftAnchor, clearAnchor } = selection;
  const selectionAnchor = useMemo(() => ({ shift: shiftAnchor, clear: clearAnchor }), [shiftAnchor, clearAnchor]);

  // The handlers below run after the aliases or facets load, by when buffer and facets (declared
  // further down, because they need the aliases) exist
  const sources = useSourcesConfig({
    onLoaded: (aliases, dockerAliases) => buffer.reapplyRowAliases(aliases, dockerAliases),
    onChanged: (change: SourcesChange) => {
      buffer.remapRows(change.remapRow);
      facets.applySourcesChange(change);
      // Keep the host filter on a renamed or removed alias
      setFilters((prev) => renameFilterHosts(prev, change.hostReplacements));
    },
  });

  const handleFacetsLoaded = (facets: LogFacetsResponse) => {
    const dockerAliases = sources.applyFormerDockerAliases(facets.former_docker_aliases ?? []);
    buffer.restoreRowDockerAliases(dockerAliases);
    // Only the apps come from stored rows alone: the hosts include configured syslog aliases and
    // Docker hosts (the Primary Docker host always exists), so they say nothing about stored logs
    const hasStoredApps = (facets.apps?.length ?? 0) > 0;
    setDatabaseHasLogs((prev) => prev === true || hasStoredApps);
  };

  const facets = useLogFacets({
    aliases: sources.aliases,
    aliasesRef: sources.aliasesRef,
    dockerAliasesRef: sources.dockerAliasesRef,
    activeHosts: activeSources,
    activeApps,
    onLoaded: handleFacetsLoaded,
  });

  const handleFirstPage = useCallback((page: { total: number; logs: LogEntry[] }, pageFilters: LogFilterParams) => {
    // An unfiltered page counts every stored row, so its total says whether the database has logs
    if (!hasActiveLogFilters(pageFilters)) {
      setDatabaseHasLogs((prev) => prev === true || page.total > 0 || page.logs.length > 0);
    }
    // New results for new filters: rank the Quick Filter chips again from them
    chipRerankRef.current = true;
  }, []);

  const buffer = useLogBuffer({
    filters,
    serverTimezone,
    isMobile,
    logsRef,
    aliasesRef: sources.aliasesRef,
    dockerAliasesRef: sources.dockerAliasesRef,
    selectionAnchor,
    onLogsReceived: facets.addLogs,
    onFirstPage: handleFirstPage,
  });
  const {
    logs,
    autoScroll,
    setAutoScroll,
    missedLogsCount,
    setMissedLogsCount,
    isLoadingHistory,
    isLoadingMore,
    hasMoreLogs,
    loadMoreLogs,
    parentRef,
    isAutoScrollRef,
    isTouchingRef,
    isLoadingMoreRef,
    queueLiveLog,
    dropPendingLiveLogs,
    endResumingToTop,
  } = buffer;

  useEffect(() => {
    if (logs.length > 0) setDatabaseHasLogs(true);
  }, [logs]);

  // Search query split once per change, then matched against every incoming live log
  const compiledQueryRef = useRef<CompiledSearchQuery>(compileSearchQuery(filters.query));

  useEffect(() => {
    compiledQueryRef.current = compileSearchQuery(filters.query);
  }, [filters.query]);

  const { applyFiltersNow } = buffer;
  const handleApplySavedView = useCallback((newParams: LogFilterParams) => {
    // The filter effect is the only loader, so a saved view fetches once
    applyFiltersNow();
    setFilters(withHostAppLists(newParams));
  }, [applyFiltersNow]);

  const handleFilterChange = useCallback((next: LogFilterParams) => setFilters(withHostAppLists(next)), []);

  const sourcesKey = activeSources.join(',');
  const appsKey = activeApps.join(',');

  const streamRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const streamRetryAttemptRef = useRef<number>(0);

  // Connect to SSE stream
  const eventSourceRef = useRef<EventSource | null>(null);
  useEffect(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    setStreamDisconnected(false);

    // If viewing a bounded historical range (to is specified), pause/skip live incoming SSE events
    if (filters.to) {
      return;
    }

    const params = new URLSearchParams();
    if (filters.severity_max !== undefined) params.set('severity_max', filters.severity_max.toString());
    if (sourcesKey) params.set('source', sourcesKey);
    if (appsKey) params.set('app_name', appsKey);

    const streamUrl = `/api/logs/stream${params.toString() ? `?${params.toString()}` : ''}`;
    const es = new EventSource(streamUrl);
    eventSourceRef.current = es;

    es.addEventListener('log', (event: MessageEvent) => {
      try {
        const entry: LogEntry = JSON.parse(event.data);

        // Live logs are matched against the search query here; the stream filters only the rest
        const compiledQuery = compiledQueryRef.current;
        if (compiledQuery.lowered && !matchesSearchQuery(entry, compiledQuery)) {
          return;
        }

        queueLiveLog(entry);
      } catch (e) {
        console.error('Error parsing SSE log event', e);
      }
    });

    es.onopen = () => {
      streamRetryAttemptRef.current = 0;
      setStreamDisconnected(false);
    };

    es.onerror = (e) => {
      console.warn('SSE stream error', e);
      // EventSource.CLOSED (2): the browser will not reconnect on its own (non-200 response, 401, proxy restart)
      if (es.readyState !== 2) return;
      setStreamDisconnected(true);
      if (streamRetryTimerRef.current !== null) return;
      const attempt = streamRetryAttemptRef.current++;
      const delayMs = Math.min(2000 * 2 ** attempt, 30000);
      streamRetryTimerRef.current = setTimeout(async () => {
        streamRetryTimerRef.current = null;
        try {
          const status = await getAuthStatus();
          if (!status.authenticated) {
            window.dispatchEvent(new CustomEvent('logshed:unauthorized', { detail: { message: 'Session expired' } }));
            return;
          }
        } catch {
          // apiFetch dispatches logshed:unauthorized on a 401; other errors fall through to a retry
        }
        setStreamReconnectNonce((n) => n + 1);
      }, delayMs);
    };

    return () => {
      if (streamRetryTimerRef.current !== null) {
        clearTimeout(streamRetryTimerRef.current);
        streamRetryTimerRef.current = null;
      }
      es.close();
      eventSourceRef.current = null;
      dropPendingLiveLogs();
    };
  }, [filters.severity_max, filters.to, sourcesKey, appsKey, queueLiveLog, dropPendingLiveLogs, streamReconnectNonce]);

  const reconnectStream = () => {
    if (streamRetryTimerRef.current !== null) {
      clearTimeout(streamRetryTimerRef.current);
      streamRetryTimerRef.current = null;
    }
    streamRetryAttemptRef.current = 0;
    setStreamReconnectNonce((n) => n + 1);
  };

  // When the cursor last scrolled the list: reaching the top that way (Home) keeps auto-scroll paused
  const cursorScrollAtRef = useRef<number>(0);

  // Scroll detection to pause auto-scroll when scrolling down, and load more when near bottom
  const handleScroll = () => {
    if (!parentRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = parentRef.current;
    const isAtTop = scrollTop <= 5;

    // Our own scroll anchoring is not the user scrolling
    const anchored = buffer.anchoredScrollTopRef.current;
    buffer.anchoredScrollTopRef.current = null;
    if (anchored === null || Math.abs(scrollTop - anchored) > 1) buffer.noteScrollActivity();

    if (isAtTop) {
      endResumingToTop();
      const scrolledByCursor = performance.now() - cursorScrollAtRef.current < 500;
      if (!autoScroll && !isTouchingRef.current && !scrolledByCursor) {
        setAutoScroll(true);
        setMissedLogsCount(0);
      }
    } else if (!buffer.resumingToTopRef.current) {
      if (autoScroll) {
        isAutoScrollRef.current = false;
        setAutoScroll(false);
      }
    }

    // If operator scrolled near bottom (< 300px from bottom), load older logs
    if (scrollHeight - scrollTop - clientHeight < 300) {
      if (hasMoreLogs && !isLoadingMoreRef.current && !isLoadingHistory) {
        loadMoreLogs();
      }
    }
  };

  // Virtualizer with fixed row heights (mobile cards or desktop rows)
  const rowVirtualizer = useVirtualizer({
    count: logs.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => (isMobile ? MOBILE_LOG_ROW_HEIGHT : DESKTOP_LOG_ROW_HEIGHT),
    overscan: isMobile ? 12 : 25,
    // Key rows by log id: prepending a batch then leaves existing rows (and LogRow memo) untouched
    getItemKey: (index) => logs[index]?.id ?? index,
  });

  // Cached row sizes come from the previous layout's estimate; re-measure when
  // crossing the mobile breakpoint so cards don't overlap at 28px offsets.
  useEffect(() => {
    rowVirtualizer.measure();
  }, [isMobile, rowVirtualizer]);

  // Keyboard row cursor. The list keeps DOM focus (rows mount and unmount as it scrolls) and
  // names the cursor row with aria-activedescendant. Tracked by log id, so it stays on its log
  // when live rows are added above it; -1 when unset or the log has left the buffer.
  const [cursorLogId, setCursorLogId] = useState<number | null>(null);
  // The cursor is outlined only while the keyboard is in use: a key on the list shows it, a mouse
  // press on the list hides it. A mouse user who opens or ticks rows never sees it.
  const [cursorOutlined, setCursorOutlined] = useState<boolean>(false);
  const cursorIndex = useMemo(
    () => (cursorLogId === null ? -1 : logs.findIndex((l) => l.id === cursorLogId)),
    [cursorLogId, logs],
  );

  // The cursor follows the log open in the detail drawer (also when it moves to the newer or older
  // log there), so closing the drawer leaves the cursor on the last log viewed
  useEffect(() => {
    if (activeLogDetail) setCursorLogId(activeLogDetail.id);
  }, [activeLogDetail]);

  const handleListKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    // Only keys pressed on the list itself, not on buttons inside it (such as the empty-state ones)
    if (e.target !== e.currentTarget || e.ctrlKey || e.metaKey || e.altKey) return;
    const count = logs.length;
    if (count === 0) return;
    const rowHeight = isMobile ? MOBILE_LOG_ROW_HEIGHT : DESKTOP_LOG_ROW_HEIGHT;
    const pageRows = Math.max(1, Math.floor(e.currentTarget.clientHeight / rowHeight) - 1);
    // Without a cursor, the first key lands on the first row in view
    const scrollTop = e.currentTarget.scrollTop;
    const firstInView = rowVirtualizer.getVirtualItems().find((item) => item.start + item.size > scrollTop)?.index ?? 0;
    const current = cursorIndex >= 0 ? cursorIndex : null;
    let next: number;
    if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', 'Enter', ' '].includes(e.key)) {
      setCursorOutlined(true);
    }
    switch (e.key) {
      case 'ArrowDown':
        next = current === null ? firstInView : current + 1;
        break;
      case 'ArrowUp':
        next = current === null ? firstInView : current - 1;
        break;
      case 'PageDown':
        next = (current ?? firstInView) + pageRows;
        break;
      case 'PageUp':
        next = (current ?? firstInView) - pageRows;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = count - 1;
        break;
      case 'Enter':
        if (current === null) return;
        e.preventDefault();
        setActiveLogDetail(logs[current]);
        return;
      case ' ':
        if (current === null) return;
        e.preventDefault();
        selection.toggleSelectLog(logs[current], current, e.shiftKey);
        return;
      default:
        return;
    }
    e.preventDefault();
    const index = Math.min(count - 1, Math.max(0, next));
    // Moving the cursor pauses auto-scroll, as scrolling down does, so live rows arriving above it
    // keep the view (and the cursor row) in place instead of carrying it down the list
    endResumingToTop();
    buffer.pauseAutoScroll();
    setCursorLogId(logs[index].id);
    cursorScrollAtRef.current = performance.now();
    rowVirtualizer.scrollToIndex(index, { align: 'auto' });
  };
  // aria-activedescendant may only name a row that is in the DOM
  const cursorRowRendered =
    cursorIndex >= 0 && rowVirtualizer.getVirtualItems().some((item) => item.index === cursorIndex);

  // Scroll the focused log into view once it is loaded, paging older logs (newest first) until it is found
  useEffect(() => {
    if (focusLogId === null || focusScrolledRef.current || isLoadingHistory || logs.length === 0) return;
    const index = logs.findIndex((l) => l.id === focusLogId);
    if (index >= 0) {
      focusScrolledRef.current = true;
      // Stop the initial load from scrolling back to the top, and keep live rows from moving the list
      isAutoScrollRef.current = false;
      setAutoScroll(false);
      rowVirtualizer.scrollToIndex(index, { align: 'center' });
      return;
    }
    if (hasMoreLogs && !isLoadingMore && focusPagesLoadedRef.current < MAX_FOCUS_PAGES) {
      focusPagesLoadedRef.current += 1;
      isAutoScrollRef.current = false;
      loadMoreLogs();
    }
  }, [focusLogId, logs, isLoadingHistory, hasMoreLogs, isLoadingMore, loadMoreLogs, rowVirtualizer, isAutoScrollRef, setAutoScroll]);

  const virtualItems = rowVirtualizer.getVirtualItems();
  const lastVirtualItem = virtualItems.length > 0 ? virtualItems[virtualItems.length - 1] : null;

  const lastVirtualItemIndex = lastVirtualItem?.index;

  useEffect(() => {
    if (lastVirtualItemIndex === undefined) return;
    if (
      lastVirtualItemIndex >= logs.length - 15 &&
      hasMoreLogs &&
      !isLoadingMoreRef.current &&
      !isLoadingHistory
    ) {
      loadMoreLogs();
    }
  }, [lastVirtualItemIndex, logs.length, hasMoreLogs, isLoadingHistory, loadMoreLogs, isLoadingMoreRef]);

  const toggleQuickSource = (src: string) => {
    setFilters((prev) => withHosts(prev, toggleListValue(hostFilterList(prev), src)));
  };

  const toggleQuickApp = (app: string) => {
    setFilters((prev) => withApps(prev, toggleListValue(appFilterList(prev), app)));
  };

  const chips = useQuickFilterChips({
    logs,
    filters,
    activeHosts: activeSources,
    activeApps,
    allAvailableSources: facets.allAvailableSources,
    allAvailableApps: facets.allAvailableApps,
    availableAppsForSelectedHosts: facets.availableAppsForSelectedHosts,
    availableSourcesForSelectedApps: facets.availableSourcesForSelectedApps,
    rerankRef: chipRerankRef,
  });

  // Saved views are shared by the search bar (Views menu, mobile drawer) and the pinned chips below
  const savedViews = useSavedViews();
  const pinnedViews = useMemo(() => savedViews.views.filter((v) => v.is_pinned), [savedViews.views]);
  const activeSavedView = useMemo(() => findActiveView(savedViews.views, filters), [savedViews.views, filters]);

  const filtersActive = useMemo(() => hasActiveLogFilters(filters), [filters]);

  const { selectedLogIds } = selection;
  const selectedLogs = useMemo(() => {
    if (selectedLogIds.size === 0) return EMPTY_SELECTED_LOGS;
    return logs.filter((l) => selectedLogIds.has(l.id));
  }, [logs, selectedLogIds]);

  const handleLaunchAiAnalysis = () => {
    if (selectedLogs.length > 0) {
      onDiagnoseAi(selectedLogs);
    }
  };

  const handleDeleteSelected = async () => {
    const targetIds = new Set(selectedLogIds);
    await deleteLogs({ log_ids: Array.from(targetIds) });
    buffer.removeRows(targetIds);
    selection.deselectAll();
    setShowDeleteSelectedConfirm(false);
  };

  const clearLogsBuffer = () => {
    buffer.clear();
    chipRerankRef.current = true;
    selection.deselectAll();
  };

  const { scrollToTopLive } = buffer;
  const { deselectAll } = selection;
  const handleResetFilters = useCallback(() => {
    setFilters({});
    deselectAll();
    scrollToTopLive('instant');
  }, [deselectAll, scrollToTopLive]);

  // The logs fetched around one in the drawer join the list (in time order) and become the selection
  const selectAndAnalyze = (target: LogEntry[]) => {
    buffer.mergeRows(target);
    selection.selectOnly(target);
    onDiagnoseAi(target);
  };

  // The drawer's log in the list (newest first): Newer is the row above, Older the row below
  const detailIndex = activeLogDetail ? logs.findIndex((l) => l.id === activeLogDetail.id) : -1;

  const emptyKind: ConsoleEmptyKind | null =
    isLoadingHistory && logs.length === 0
      ? 'loading'
      : logs.length > 0
      ? null
      : filtersActive
      ? 'no-match'
      : databaseHasLogs === false
      ? 'first-run'
      : 'waiting';

  const streamToolbar = (
    <StreamToolbar
      isMobile={isMobile}
      pinnedViews={pinnedViews}
      activeSavedView={activeSavedView}
      onApplySavedView={handleApplySavedView}
      onResetFilters={handleResetFilters}
      hostChips={chips.hostChips}
      appChips={chips.appChips}
      activeHosts={activeSources}
      activeApps={activeApps}
      selectableHosts={chips.selectableHosts}
      onToggleHost={toggleQuickSource}
      onToggleApp={toggleQuickApp}
      bufferSize={logs.length}
      hasSelection={selectedLogIds.size > 0}
      onSelectAll={selection.selectAll}
      onDeselectAll={selection.deselectAll}
      historicalRange={Boolean(filters.to)}
      autoScroll={autoScroll}
      onToggleAutoScroll={() => (autoScroll ? setAutoScroll(false) : buffer.resumeAutoScroll())}
      onClearBuffer={clearLogsBuffer}
    />
  );

  return (
    <div className="flex flex-col h-full min-h-0 flex-1 bg-canvas select-text overflow-hidden">
      {/* Top Filter & Search Controls (Supports Pull-to-Refresh on Mobile) */}
      <div {...(isMobile && pullTouchHandlers ? pullTouchHandlers : {})} className="shrink-0 select-none">
        {/* Search & Filter Bar with Integrated Saved Views */}
        <LogSearchBar
          timeZone={serverTimezone || undefined}
          filters={filters}
          onFilterChange={handleFilterChange}
          onSearch={() => buffer.loadInitialLogs()}
          onReset={handleResetFilters}
          availableSources={facets.availableSourcesForSelectedApps}
          availableApps={facets.availableAppsForSelectedHosts}
          onApplySavedView={handleApplySavedView}
          savedViews={savedViews}
          totalCount={buffer.totalCount ?? undefined}
          totalCapped={buffer.totalCapped}
          facetsTruncated={facets.facetsTruncated}
          facetsLimit={facets.facetsLimit}
          mobileToolbar={isMobile ? streamToolbar : undefined}
        />

        {/* Stream Controls & Filter Pills Bar (on phones it is part of the search bar) */}
        {!isMobile && (
          <div className="bg-surface px-3 py-1.5 border-b border-line flex flex-nowrap items-center justify-between gap-2 text-xs select-none relative overflow-hidden">
            {streamToolbar}
          </div>
        )}
      </div>

      {/* Virtualized Table View */}
      <div className="flex-1 relative overflow-hidden flex flex-col">
        {/* Table Header (Desktop only) */}
        {!isMobile && (
          <div
            data-testid="log-table-header"
            className={`grid bg-canvas border-b border-line border-l-2 border-l-transparent text-fg-muted text-[11px] font-mono font-semibold ${logGridCols(aiEnabled)} px-3 py-1.5 select-none items-center`}
          >
            <div className="flex items-center justify-center">
              <button
                onClick={selectedLogIds.size > 0 ? selection.deselectAll : selection.selectAll}
                disabled={logs.length === 0}
                title={selectedLogIds.size > 0 ? 'Deselect all rows' : (logs.length > SELECT_ALL_LIMIT ? `Select all rows (capped at ${SELECT_ALL_LIMIT})` : 'Select all rows')}
                aria-label={selectedLogIds.size > 0 ? 'Toggle deselect all in table' : 'Toggle select all in table'}
                className="p-1 hover:text-fg transition disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
              >
                {selectedLogIds.size > 0 ? (
                  <CheckSquare className="w-3.5 h-3.5 text-accent-400" />
                ) : (
                  <Square className="w-3.5 h-3.5 opacity-50 hover:opacity-100" />
                )}
              </button>
            </div>
            <div>TIMESTAMP</div>
            <div>SEV</div>
            <div>HOST / IP</div>
            <div>APP / CONTAINER</div>
            <div>MESSAGE</div>
            {aiEnabled && <div className="text-right pr-2">ACTIONS</div>}
          </div>
        )}

        {buffer.loadError && (
          <div role="alert" className="shrink-0 px-3 py-1.5 bg-danger-950/60 border-b border-danger-800 text-xs text-danger-300 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-danger-400 shrink-0" />
            <span>{buffer.loadError}</span>
          </div>
        )}

        {streamDisconnected && !filters.to && (
          <div role="status" className="shrink-0 px-3 py-1.5 bg-warning-950/60 border-b border-warning-800 text-xs text-warning-300 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-warning-400 shrink-0" />
            <span>Live stream disconnected</span>
            <button
              type="button"
              onClick={reconnectStream}
              className="underline hover:text-fg-max cursor-pointer font-medium"
            >
              Reconnect
            </button>
          </div>
        )}

        {/* Scrollable Virtualized Area */}
        <div
          ref={parentRef}
          onScroll={handleScroll}
          onTouchStart={() => {
            isTouchingRef.current = true;
            endResumingToTop();
          }}
          onTouchEnd={() => {
            isTouchingRef.current = false;
            buffer.noteScrollActivity();
          }}
          onTouchCancel={() => {
            isTouchingRef.current = false;
            buffer.noteScrollActivity();
          }}
          onWheel={endResumingToTop}
          onMouseDown={() => {
            endResumingToTop();
            setCursorOutlined(false);
          }}
          // One Tab stop: the arrow, page, Home and End keys move the row cursor (handleListKeyDown)
          tabIndex={0}
          onKeyDown={handleListKeyDown}
          role={logs.length > 0 ? 'listbox' : 'region'}
          aria-label="Log list"
          aria-multiselectable={logs.length > 0 ? true : undefined}
          aria-activedescendant={cursorRowRendered ? logRowId(logs[cursorIndex].id) : undefined}
          className="group/loglist flex-1 overflow-y-auto overflow-x-hidden font-mono text-xs bg-canvas relative overscroll-contain focus:outline-hidden focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-accent-500"
        >
          {emptyKind !== null ? (
            <ConsoleEmptyState
              kind={emptyKind}
              onResetFilters={handleResetFilters}
              syslogPort={syslogPort}
              onOpenSourcesSettings={onOpenSourcesSettings}
            />
          ) : (
            <div
              style={{
                height: `${rowVirtualizer.getTotalSize()}px`,
                width: '100%',
                position: 'relative',
              }}
            >
              {virtualItems.map((virtualRow) => {
                const log = logs[virtualRow.index];
                if (!log) return null;

                return (
                  <LogRow
                    key={virtualRow.key}
                    log={log}
                    index={virtualRow.index}
                    isSelected={selectedLogIds.has(log.id)}
                    isMobile={isMobile}
                    aiEnabled={aiEnabled}
                    isFocused={log.id === focusLogId}
                    isCursor={cursorOutlined && virtualRow.index === cursorIndex}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      height: `${virtualRow.size}px`,
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                    onSelect={selection.toggleSelectLog}
                    onClick={setActiveLogDetail}
                    onDiagnoseAi={onDiagnoseAi}
                  />
                );
              })}
            </div>
          )}

          {/* Infinite Scroll Footer: Loading Spinner or End of Range */}
          {logs.length > 0 && isLoadingMore && (
            <div className="py-2.5 flex items-center justify-center gap-2 text-fg-muted font-mono text-xs bg-canvas/90 border-t border-surface">
              <Loader2 className="w-3.5 h-3.5 animate-spin text-accent-400" />
              <span>Loading older logs...</span>
            </div>
          )}

          {logs.length > 0 && !hasMoreLogs && !isLoadingHistory && (
            <div className="py-2.5 flex items-center justify-center text-fg-muted font-mono text-[11px] bg-canvas border-t border-surface select-none">
             - Reached the end of loaded history ({buffer.totalCapped ? '1,000+ logs' : `${logs.length.toLocaleString()} log${logs.length === 1 ? '' : 's'}`} loaded) -
            </div>
          )}

          {logs.length >= SCROLLBACK_BUFFER_SIZE && hasMoreLogs && !isLoadingMore && (
            <div className="py-2.5 flex items-center justify-center text-fg-muted font-mono text-[11px] bg-canvas border-t border-surface select-none text-center px-3">
              Screen buffer limit reached ({SCROLLBACK_BUFFER_SIZE.toLocaleString()} lines). Narrow the filters or time range to see older logs.
            </div>
          )}
        </div>

        {/* Floating Busy Indicator: filter/search reload in flight while previous results remain visible */}
        {isLoadingHistory && logs.length > 0 && (
          <div
            role="status"
            aria-live="polite"
            className="absolute top-10 left-1/2 -translate-x-1/2 z-20 pointer-events-none bg-surface/95 border border-line text-fg-soft text-xs font-mono px-3 py-1.5 rounded-full flex items-center gap-2 shadow-lg"
          >
            <Loader2 className="w-3.5 h-3.5 animate-spin text-accent-400" />
            <span>Updating results...</span>
          </div>
        )}

        {/* Floating Pause/Resume Banner */}
        {!autoScroll && missedLogsCount > 0 && (
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 shadow-xl w-[92%] sm:w-auto max-w-md flex justify-center">
            <button
              onClick={buffer.resumeAutoScroll}
              className="w-full sm:w-auto bg-accent-700 hover:bg-accent-600 text-on-fill text-xs font-semibold px-4 py-2 rounded-xl sm:rounded-full flex items-center justify-center gap-2 shadow-lg transition"
            >
              <ArrowUp className="w-3.5 h-3.5 animate-bounce shrink-0" />
              <div className="flex flex-col items-center justify-center text-center leading-tight">
                <span>Auto-scroll paused ({missedLogsCount} new log{missedLogsCount === 1 ? '' : 's'} at top)</span>
                <span className="text-accent-200 text-[11px] font-normal">Click to jump to top</span>
              </div>
            </button>
          </div>
        )}
      </div>

      {/* Floating Multi-Select Action Bar */}
      {selectedLogs.length > 0 && (
        <SelectionActionBar
          selectedLogs={selectedLogs}
          aiEnabled={aiEnabled}
          onDeselectAll={selection.deselectAll}
          onDelete={() => setShowDeleteSelectedConfirm(true)}
          onAnalyze={handleLaunchAiAnalysis}
        />
      )}

      {/* Log Detail Slide-Over Inspector */}
      {activeLogDetail && (
        <LogDetailModal
          log={activeLogDetail}
          isOpen={Boolean(activeLogDetail)}
          onClose={() => setActiveLogDetail(null)}
          timeZone={serverTimezone}
          aiEnabled={aiEnabled}
          // The AI dialog opens on top of the drawer, so closing it shows the same log again
          onExplainWithAi={(log, ctxLogs) => selectAndAnalyze(ctxLogs && ctxLogs.length > 0 ? ctxLogs : [log])}
          onInspectWithContext={selectAndAnalyze}
          onAddAlias={onAddAlias}
          onEditDockerHost={onEditDockerHost}
          onManageDeletedDockerHost={onManageDeletedDockerHost}
          dockerEndpoints={sources.dockerEndpoints}
          formerDockerAliases={sources.formerDockerAliases}
          isHostAliased={Boolean(
            sources.aliases[activeLogDetail.source_ip] ||
            (activeLogDetail.source_alias && activeLogDetail.source_alias !== activeLogDetail.source_ip)
          )}
          onCreateDropRule={(targetLog) => {
            setActiveLogDetail(null);
            setDropRuleTargetLog(targetLog);
          }}
          // The prefill goes through memory, not the URL: messages are long and may hold sensitive text
          onCreateAlertRule={(targetLog) => {
            setActiveLogDetail(null);
            setAlertRuleHandoff({
              appName: targetLog.app_name || undefined,
              message: cleanLogMessageForDisplay(targetLog.message),
            });
            window.history.pushState(null, '', '/rules/rules');
            window.dispatchEvent(new PopStateEvent('popstate'));
          }}
          onDeleteLog={(deletedLog) => {
            buffer.removeRows(new Set([deletedLog.id]));
            selection.forget([deletedLog.id]);
          }}
          onNavigateNewer={() => {
            if (detailIndex > 0) setActiveLogDetail(logs[detailIndex - 1]);
          }}
          onNavigateOlder={() => {
            if (detailIndex >= 0 && detailIndex < logs.length - 1) setActiveLogDetail(logs[detailIndex + 1]);
          }}
          hasNewerLog={detailIndex > 0}
          hasOlderLog={detailIndex >= 0 && detailIndex < logs.length - 1}
        />
      )}

      {/* Create Drop Rule Modal */}
      {dropRuleTargetLog && (
        <React.Suspense fallback={null}>
          <CreateDropRuleModal
            isOpen={Boolean(dropRuleTargetLog)}
            onClose={() => setDropRuleTargetLog(null)}
            initialSource={dropRuleTargetLog.source_alias || dropRuleTargetLog.source_ip}
            initialApp={dropRuleTargetLog.app_name}
            initialMessage={cleanLogMessageForDisplay(dropRuleTargetLog.message)}
            availableSources={facets.availableSourcesForSelectedApps}
            availableApps={facets.availableAppsForSelectedHosts}
          />
        </React.Suspense>
      )}

      {/* Delete Selected Confirmation Modal */}
      {showDeleteSelectedConfirm && (
        <DeleteSelectedDialog
          count={selectedLogs.length}
          onClose={() => setShowDeleteSelectedConfirm(false)}
          onDelete={handleDeleteSelected}
        />
      )}
    </div>
  );
};
