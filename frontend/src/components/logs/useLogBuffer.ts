import { MutableRefObject, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { LogEntry, LogFilterParams } from '../../types.ts';
import { fetchLogs } from '../../api/logs.ts';
import { getTodayKey } from '../../utils/formatters.ts';
import {
  ProcessedLogEntry,
  mergeRowsInTimeOrder,
  prepareLogEntry,
  reapplyAliases,
  reformatTimestamps,
  restoreDockerAliases,
} from '../../utils/logEntry.ts';
import { DESKTOP_LOG_ROW_HEIGHT, MOBILE_LOG_ROW_HEIGHT } from './LogRow.tsx';

// Rows kept while auto-scroll is on, and while the user is scrolled back reading history
export const LIVE_BUFFER_SIZE = 3000;
export const SCROLLBACK_BUFFER_SIZE = 20000;
// Logs per history page
const PAGE_SIZE = 500;
const BATCH_FLUSH_INTERVAL_MS = 100;
// How long after the last scroll event (or a finger lifting) scrolling counts as stopped
const SCROLL_IDLE_MS = 150;
// Longest the Paused and jump-to-top buttons' smooth scroll to the top may take
const RESUME_SCROLL_MAX_MS = 1500;
const SEARCH_DEBOUNCE_MS = 300;

interface LogBufferOptions {
  filters: LogFilterParams;
  serverTimezone: string;
  isMobile: boolean;
  /** The list as last rendered, kept up to date by this hook (shared with the selection) */
  logsRef: MutableRefObject<ProcessedLogEntry[]>;
  /** Latest host aliases and Docker aliases, used to prepare each row */
  aliasesRef: MutableRefObject<Record<string, string>>;
  dockerAliasesRef: MutableRefObject<Set<string>>;
  /** Moves or forgets the selection's range anchor when rows are added above it or the list is replaced */
  selectionAnchor: { shift: (rowsAdded: number) => void; clear: () => void };
  /**
   * Called with every history page and live batch before it is shown (the facets grow from them),
   * and, for the first page of a load, with that page's response and the filters it used.
   * Both must keep their identity across renders.
   */
  onLogsReceived: (logs: LogEntry[]) => void;
  onFirstPage: (page: { total: number; logs: LogEntry[] }, filters: LogFilterParams) => void;
}

/**
 * The rows of the Console list and how they arrive: the first history page for the filters (search
 * edits debounced), older pages on demand, and live logs in batches. Live rows go on top; while the
 * user is scrolled back they keep the view still, and while the user is scrolling, or the scrollback
 * buffer is full, they are held and counted until scrolling stops or auto-scroll resumes. The list
 * keeps the newest LIVE_BUFFER_SIZE rows with auto-scroll on and up to SCROLLBACK_BUFFER_SIZE while
 * scrolled back.
 */
export function useLogBuffer({
  filters,
  serverTimezone,
  isMobile,
  logsRef,
  aliasesRef,
  dockerAliasesRef,
  selectionAnchor,
  onLogsReceived,
  onFirstPage,
}: LogBufferOptions) {
  const [logs, setLogs] = useState<ProcessedLogEntry[]>([]);
  const [autoScroll, setAutoScroll] = useState<boolean>(true);
  const [missedLogsCount, setMissedLogsCount] = useState<number>(0);
  const [isLoadingHistory, setIsLoadingHistory] = useState<boolean>(false);
  const [isLoadingMore, setIsLoadingMore] = useState<boolean>(false);
  const [hasMoreLogs, setHasMoreLogs] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [totalCount, setTotalCount] = useState<number | null>(null);
  const [totalCapped, setTotalCapped] = useState<boolean>(false);
  const historicalOffsetRef = useRef<number>(0);
  const isLoadingMoreRef = useRef<boolean>(false);

  useEffect(() => {
    logsRef.current = logs;
  }, [logs, logsRef]);

  // Time zone and calendar day the buffer's display timestamps were last formatted with
  const formatTzRef = useRef<string>(serverTimezone);
  const formatDayRef = useRef<string>(getTodayKey(serverTimezone || undefined));
  const getFormatContext = useCallback(() => {
    const timeZone = formatTzRef.current || undefined;
    return { timeZone, todayKey: getTodayKey(timeZone) };
  }, []);

  // Rows loaded before the server time zone arrived were formatted in browser time: re-format them
  useEffect(() => {
    if (serverTimezone === formatTzRef.current) return;
    formatTzRef.current = serverTimezone;
    const fmt = getFormatContext();
    formatDayRef.current = fmt.todayKey;
    setLogs((prev) => reformatTimestamps(prev, fmt.timeZone, fmt.todayKey));
  }, [serverTimezone, getFormatContext]);

  /** Logs as list rows, with the latest aliases and the current time zone (read once per batch). */
  const prepareRows = useCallback((entries: LogEntry[]): ProcessedLogEntry[] => {
    const fmt = getFormatContext();
    return entries.map((entry) =>
      prepareLogEntry(entry, aliasesRef.current, fmt.timeZone, dockerAliasesRef.current, fmt.todayKey)
    );
  }, [getFormatContext, aliasesRef, dockerAliasesRef]);

  // Incoming SSE batch buffer & flush timer
  const incomingBufferRef = useRef<LogEntry[]>([]);
  // Live rows held back while scrolled back with a full scrollback buffer (newest first)
  const heldLiveRowsRef = useRef<ProcessedLogEntry[]>([]);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Read by flushIncomingLogs, which is cached by useCallback and would otherwise keep the first layout's row height
  const isMobileRef = useRef<boolean>(isMobile);
  isMobileRef.current = isMobile;
  const parentRef = useRef<HTMLDivElement>(null);
  const isAutoScrollRef = useRef<boolean>(true);
  const pendingScrollAdjustmentRef = useRef<number>(0);
  const isTouchingRef = useRef<boolean>(false);
  // True from a scroll event (or a finger lifting) until SCROLL_IDLE_MS pass without another one, so a
  // momentum glide counts as scrolling. Mobile browsers show a scrollTop write during a drag or glide as a jump.
  const isScrollingRef = useRef<boolean>(false);
  const scrollIdleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The scrollTop our own anchoring wrote, so its scroll event is not taken for the user's
  const anchoredScrollTopRef = useRef<number | null>(null);
  // Set when held rows were dropped (the scrollback buffer was full): they then wait for auto-scroll to
  // resume, because joining them to the list would leave a gap
  const heldRowsDroppedRef = useRef<boolean>(false);
  // Set by the Paused and jump-to-top buttons while the smooth scroll to the top runs, so its scroll
  // events do not pause auto-scroll again on the way up
  const resumingToTopRef = useRef<boolean>(false);
  const resumingToTopTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Keep ref updated
  useEffect(() => {
    isAutoScrollRef.current = autoScroll;
  }, [autoScroll]);

  // Buffer cap, one rule for live rows and history pages: keep the newest LIVE_BUFFER_SIZE rows
  // while auto-scroll is on, and up to SCROLLBACK_BUFFER_SIZE while scrolled back. Trimming drops the
  // oldest rows, so the next history page must start that many rows earlier. Live batches trim
  // themselves in flushIncomingLogs; this catches history pages and auto-scroll turning back on.
  useLayoutEffect(() => {
    const cap = autoScroll ? LIVE_BUFFER_SIZE : SCROLLBACK_BUFFER_SIZE;
    if (logs.length <= cap) return;
    const removed = logs.length - cap;
    historicalOffsetRef.current = Math.max(0, historicalOffsetRef.current - removed);
    setHasMoreLogs(true);
    setLogs(logs.slice(0, cap));
  }, [logs, autoScroll]);

  // Rows held back while the scrollback buffer was full join the top once auto-scroll resumes
  useEffect(() => {
    if (!autoScroll || heldLiveRowsRef.current.length === 0) return;
    const held = heldLiveRowsRef.current;
    heldLiveRowsRef.current = [];
    heldRowsDroppedRef.current = false;
    setLogs((prev) => {
      const existingIds = new Set(prev.map((l) => l.id));
      return [...held.filter((l) => !existingIds.has(l.id)), ...prev];
    });
    selectionAnchor.clear();
  }, [autoScroll, selectionAnchor]);

  // Maintain scroll anchoring when new logs arrive while auto-scroll is paused
  useLayoutEffect(() => {
    if (pendingScrollAdjustmentRef.current > 0) {
      const adjustment = pendingScrollAdjustmentRef.current;
      pendingScrollAdjustmentRef.current = 0;
      if (parentRef.current && !isAutoScrollRef.current) {
        parentRef.current.scrollTop += adjustment;
        anchoredScrollTopRef.current = parentRef.current.scrollTop;
      }
    }
  }, [logs]);

  // Put prepared live rows (newest first) at the top of the list
  const prependLiveRows = useCallback((prepared: ProcessedLogEntry[]) => {
    // "Today" is checked once per batch; after midnight the rows already shown get their date back
    const fmt = getFormatContext();
    const dayChanged = fmt.todayKey !== formatDayRef.current;
    formatDayRef.current = fmt.todayKey;

    // Trim to the buffer cap here rather than leaving it to the cap effect, which would render every
    // batch twice once the buffer is full: once grown, then again trimmed
    const cap = isAutoScrollRef.current ? LIVE_BUFFER_SIZE : SCROLLBACK_BUFFER_SIZE;
    const removed = logsRef.current.length + prepared.length - cap;
    if (removed > 0) {
      historicalOffsetRef.current = Math.max(0, historicalOffsetRef.current - removed);
      setHasMoreLogs(true);
    }
    setLogs((prev) => {
      const base = dayChanged ? reformatTimestamps(prev, fmt.timeZone, fmt.todayKey) : prev;
      const next = [...prepared, ...base];
      return next.length > cap ? next.slice(0, cap) : next;
    });

    selectionAnchor.shift(prepared.length);

    if (!isAutoScrollRef.current) {
      // Anchor scroll position by compensating for prepended items' height
      pendingScrollAdjustmentRef.current += prepared.length * (isMobileRef.current ? MOBILE_LOG_ROW_HEIGHT : DESKTOP_LOG_ROW_HEIGHT);
    } else if (parentRef.current && !isTouchingRef.current) {
      requestAnimationFrame(() => {
        if (parentRef.current && isAutoScrollRef.current && !isTouchingRef.current) {
          parentRef.current.scrollTop = 0;
        }
      });
    }
  }, [getFormatContext, logsRef, selectionAnchor]);

  // Rows held while the user was scrolling join the list in one batch, with one scroll correction,
  // once scrolling has stopped. Rows held because the scrollback buffer is full wait for auto-scroll.
  const releaseHeldLiveRows = useCallback(() => {
    const held = heldLiveRowsRef.current;
    if (
      held.length === 0 ||
      isAutoScrollRef.current ||
      isTouchingRef.current ||
      isScrollingRef.current ||
      heldRowsDroppedRef.current ||
      logsRef.current.length + held.length > SCROLLBACK_BUFFER_SIZE
    ) {
      return;
    }
    heldLiveRowsRef.current = [];
    const existingIds = new Set(logsRef.current.map((l) => l.id));
    const fresh = held.filter((l) => !existingIds.has(l.id));
    if (fresh.length > 0) prependLiveRows(fresh);
  }, [prependLiveRows, logsRef]);

  // A scroll event or a lifted finger: scrolling counts as stopped SCROLL_IDLE_MS after the last one
  const noteScrollActivity = useCallback(() => {
    isScrollingRef.current = true;
    if (scrollIdleTimerRef.current !== null) clearTimeout(scrollIdleTimerRef.current);
    scrollIdleTimerRef.current = setTimeout(() => {
      scrollIdleTimerRef.current = null;
      isScrollingRef.current = false;
      releaseHeldLiveRows();
    }, SCROLL_IDLE_MS);
  }, [releaseHeldLiveRows]);

  useEffect(() => () => {
    if (scrollIdleTimerRef.current !== null) clearTimeout(scrollIdleTimerRef.current);
    if (resumingToTopTimerRef.current !== null) clearTimeout(resumingToTopTimerRef.current);
  }, []);

  // Flush incoming buffered SSE logs to React state in a single batch
  const flushIncomingLogs = useCallback(() => {
    if (incomingBufferRef.current.length === 0) return;

    const toFlush = incomingBufferRef.current;
    incomingBufferRef.current = [];

    // Incrementally update facets with new incoming logs
    onLogsReceived(toFlush);

    // toFlush is in arrival order (oldest first, newest last): reverse it so the newest log is at the top
    const prepared = prepareRows(toFlush).reverse();

    if (!isAutoScrollRef.current) {
      setMissedLogsCount((prev) => prev + toFlush.length);
      // Scrolled back and either still scrolling, or with a full scrollback buffer: hold new rows
      // (counted in the "N new" banner). Moving the list under a finger or a glide shows as a jump on
      // phones, and trimming the full buffer would cut the history the user is reading.
      const bufferFull = logsRef.current.length + heldLiveRowsRef.current.length + prepared.length > SCROLLBACK_BUFFER_SIZE;
      if (heldLiveRowsRef.current.length > 0 || bufferFull || isTouchingRef.current || isScrollingRef.current) {
        const held = [...prepared, ...heldLiveRowsRef.current];
        if (bufferFull && held.length > LIVE_BUFFER_SIZE) {
          heldRowsDroppedRef.current = true;
          heldLiveRowsRef.current = held.slice(0, LIVE_BUFFER_SIZE);
        } else {
          heldLiveRowsRef.current = held;
        }
        return;
      }
    }

    prependLiveRows(prepared);
  }, [onLogsReceived, prepareRows, prependLiveRows, logsRef]);

  /** Queues a live log; queued logs join the list together, at most BATCH_FLUSH_INTERVAL_MS later. */
  const queueLiveLog = useCallback((entry: LogEntry) => {
    incomingBufferRef.current.push(entry);

    if (incomingBufferRef.current.length >= PAGE_SIZE) {
      if (flushTimerRef.current !== null) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      flushIncomingLogs();
    } else if (flushTimerRef.current === null) {
      flushTimerRef.current = setTimeout(() => {
        flushTimerRef.current = null;
        flushIncomingLogs();
      }, BATCH_FLUSH_INTERVAL_MS);
    }
  }, [flushIncomingLogs]);

  /** Drops queued and held live logs, for a stream that closed or a list that is replaced. */
  const dropPendingLiveLogs = useCallback(() => {
    if (flushTimerRef.current !== null) {
      clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    incomingBufferRef.current = [];
    heldLiveRowsRef.current = [];
    heldRowsDroppedRef.current = false;
  }, []);

  // In-flight query cancellation / stale response guard
  const fetchRequestIdRef = useRef<number>(0);
  // Aborts the in-flight history request when a newer load supersedes it
  const fetchAbortRef = useRef<AbortController | null>(null);
  // Set by applyFiltersNow so the filter effect loads at once, without the search debounce
  const skipSearchDebounceRef = useRef<boolean>(false);
  // Pending debounced reload scheduled by free-text search edits
  const searchDebounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Abort an in-flight history request on unmount
  useEffect(() => () => fetchAbortRef.current?.abort(), []);

  // Load initial logs on mount or on filter apply
  const loadInitialLogs = useCallback(async () => {
    if (searchDebounceTimerRef.current !== null) {
      clearTimeout(searchDebounceTimerRef.current);
      searchDebounceTimerRef.current = null;
    }
    const reqId = ++fetchRequestIdRef.current;
    fetchAbortRef.current?.abort();
    const controller = new AbortController();
    fetchAbortRef.current = controller;
    try {
      setIsLoadingHistory(true);
      historicalOffsetRef.current = 0;
      setLoadError(null);
      setHasMoreLogs(true);
      dropPendingLiveLogs();
      pendingScrollAdjustmentRef.current = 0;

      const res = await fetchLogs({
        ...filters,
        limit: PAGE_SIZE,
        offset: 0,
      }, { signal: controller.signal });

      // Discard stale responses from superseded requests
      if (reqId !== fetchRequestIdRef.current) {
        return;
      }

      // Keep newest logs at the top (res.logs is ordered DESC)
      setLogs(prepareRows(res.logs));
      onFirstPage(res, filters);
      onLogsReceived(res.logs);
      historicalOffsetRef.current = res.logs.length;
      setTotalCount(res.total);
      setTotalCapped(Boolean(res.total_capped));
      if (res.logs.length < PAGE_SIZE || (res.total !== undefined && !res.total_capped && res.logs.length >= res.total)) {
        setHasMoreLogs(false);
      }
      setMissedLogsCount(0);
      selectionAnchor.clear();
      // Auto-scroll to top after initial load
      setTimeout(() => {
        if (parentRef.current && isAutoScrollRef.current) {
          parentRef.current.scrollTop = 0;
        }
      }, 50);
    } catch (err) {
      if (reqId === fetchRequestIdRef.current) {
        console.error('Failed to load initial logs', err);
        setLoadError(err instanceof Error ? err.message : 'Failed to load logs');
      }
    } finally {
      if (reqId === fetchRequestIdRef.current) {
        setIsLoadingHistory(false);
      }
    }
  }, [filters, onLogsReceived, onFirstPage, prepareRows, dropPendingLiveLogs, selectionAnchor]);

  const loadMoreLogs = useCallback(async () => {
    if (isLoadingMoreRef.current || !hasMoreLogs || isLoadingHistory) return;
    if (logsRef.current.length >= (isAutoScrollRef.current ? LIVE_BUFFER_SIZE : SCROLLBACK_BUFFER_SIZE)) return;
    isLoadingMoreRef.current = true;
    setIsLoadingMore(true);
    const reqId = fetchRequestIdRef.current;
    try {
      const currentOffset = historicalOffsetRef.current;
      const res = await fetchLogs({
        ...filters,
        limit: PAGE_SIZE,
        offset: currentOffset,
      }, { signal: fetchAbortRef.current?.signal });

      // Discard stale responses if filter changed while loading more
      if (reqId !== fetchRequestIdRef.current) {
        return;
      }

      historicalOffsetRef.current = currentOffset + res.logs.length;
      if (res.total !== undefined) {
        setTotalCount(res.total);
        setTotalCapped(Boolean(res.total_capped));
      }
      if (res.logs.length < PAGE_SIZE || (res.total !== undefined && !res.total_capped && historicalOffsetRef.current >= res.total)) {
        setHasMoreLogs(false);
      }
      if (res.logs.length > 0) {
        onLogsReceived(res.logs);
        const preparedLogs = prepareRows(res.logs);
        setLogs((prev) => {
          const existingIds = new Set(prev.map((l) => l.id));
          const uniqueIncoming = preparedLogs.filter((l) => !existingIds.has(l.id));
          if (uniqueIncoming.length === 0 && res.logs.length > 0) {
            if (res.logs.length < PAGE_SIZE) {
              setHasMoreLogs(false);
            }
            return prev;
          }
          return [...prev, ...uniqueIncoming];
        });
      }
    } catch (err) {
      if (reqId === fetchRequestIdRef.current) {
        console.error('Failed to load more historical logs', err);
      }
    } finally {
      isLoadingMoreRef.current = false;
      setIsLoadingMore(false);
    }
  }, [filters, hasMoreLogs, isLoadingHistory, onLogsReceived, prepareRows, logsRef]);

  // Reload on filter changes. Free-text query edits are debounced so search-as-you-type does not
  // dispatch a backend FTS query per keystroke (superseded queries keep running server-side);
  // structured filter changes (host, app, severity, time) reload immediately.
  const previousQueryRef = useRef<string | undefined>(filters.query);
  useEffect(() => {
    const queryChanged = previousQueryRef.current !== filters.query;
    previousQueryRef.current = filters.query;
    const skipDebounce = skipSearchDebounceRef.current;
    skipSearchDebounceRef.current = false;
    if (!queryChanged || skipDebounce) {
      loadInitialLogs();
      return;
    }
    searchDebounceTimerRef.current = setTimeout(() => {
      searchDebounceTimerRef.current = null;
      loadInitialLogs();
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      if (searchDebounceTimerRef.current !== null) {
        clearTimeout(searchDebounceTimerRef.current);
        searchDebounceTimerRef.current = null;
      }
    };
  }, [loadInitialLogs, filters.query]);

  /** The next filter change loads at once, even a search edit (a saved view is applied whole). */
  const applyFiltersNow = useCallback(() => {
    skipSearchDebounceRef.current = true;
  }, []);

  const endResumingToTop = useCallback(() => {
    resumingToTopRef.current = false;
    if (resumingToTopTimerRef.current !== null) {
      clearTimeout(resumingToTopTimerRef.current);
      resumingToTopTimerRef.current = null;
    }
  }, []);

  /** Pauses auto-scroll at once (the ref too), for a scroll down or a cursor move. */
  const pauseAutoScroll = useCallback(() => {
    if (isAutoScrollRef.current) {
      isAutoScrollRef.current = false;
      setAutoScroll(false);
    }
  }, []);

  /** Turns auto-scroll back on and scrolls to the top: smoothly, or at once for a reset. */
  const scrollToTopLive = useCallback((behavior: 'smooth' | 'instant') => {
    isAutoScrollRef.current = true;
    setAutoScroll(true);
    setMissedLogsCount(0);
    pendingScrollAdjustmentRef.current = 0;
    if (parentRef.current) {
      if (typeof parentRef.current.scrollTo === 'function') {
        parentRef.current.scrollTo({ top: 0, behavior: behavior as ScrollBehavior });
      } else {
        parentRef.current.scrollTop = 0;
      }
    }
  }, []);

  const resumeAutoScroll = useCallback(() => {
    // The smooth scroll up passes through positions that would otherwise pause auto-scroll again, and
    // a paused batch would then write scrollTop, which stops the scroll short of the top
    endResumingToTop();
    resumingToTopRef.current = true;
    // In case the scroll never reaches the top (the user takes over, or the browser cuts it short)
    resumingToTopTimerRef.current = setTimeout(endResumingToTop, RESUME_SCROLL_MAX_MS);
    scrollToTopLive('smooth');
  }, [endResumingToTop, scrollToTopLive]);

  /** Adds logs fetched around one (the drawer's context) in time order. */
  const mergeRows = useCallback((entries: LogEntry[]) => {
    setLogs((prev) => mergeRowsInTimeOrder(prev, entries, prepareRows));
  }, [prepareRows]);

  /** Takes deleted logs out of the list and the total. */
  const removeRows = useCallback((ids: Set<number>) => {
    setLogs((prev) => prev.filter((l) => !ids.has(l.id)));
    setTotalCount((prev) => (prev === null ? prev : Math.max(0, prev - ids.size)));
  }, []);

  /** Shows the rows already in the list with newly loaded aliases (the first page may predate them). */
  const reapplyRowAliases = useCallback((aliases: Record<string, string>, dockerAliases: Set<string>) => {
    setLogs((prev) => reapplyAliases(prev, aliases, dockerAliases));
  }, []);

  /** Gives Docker rows their Docker host name back once the former Docker aliases are known. */
  const restoreRowDockerAliases = useCallback((dockerAliases: Set<string>) => {
    setLogs((prev) => restoreDockerAliases(prev, dockerAliases));
  }, []);

  /** Renames the hosts of the rows already in the list. */
  const remapRows = useCallback((remap: (log: ProcessedLogEntry) => ProcessedLogEntry) => {
    setLogs((prev) => prev.map(remap));
  }, []);

  /** Empties the screen buffer; the logs stay stored. */
  const clear = useCallback(() => {
    fetchRequestIdRef.current += 1;
    fetchAbortRef.current?.abort();
    dropPendingLiveLogs();
    pendingScrollAdjustmentRef.current = 0;
    setLogs([]);
    setMissedLogsCount(0);
    setHasMoreLogs(false);
    setTotalCount(0);
    setTotalCapped(false);
  }, [dropPendingLiveLogs]);

  return {
    logs,
    autoScroll,
    setAutoScroll,
    missedLogsCount,
    setMissedLogsCount,
    isLoadingHistory,
    isLoadingMore,
    hasMoreLogs,
    loadError,
    totalCount,
    totalCapped,
    parentRef,
    isAutoScrollRef,
    isTouchingRef,
    isLoadingMoreRef,
    anchoredScrollTopRef,
    resumingToTopRef,
    loadInitialLogs,
    loadMoreLogs,
    applyFiltersNow,
    queueLiveLog,
    dropPendingLiveLogs,
    noteScrollActivity,
    endResumingToTop,
    pauseAutoScroll,
    resumeAutoScroll,
    scrollToTopLive,
    mergeRows,
    removeRows,
    reapplyRowAliases,
    restoreRowDockerAliases,
    remapRows,
    clear,
  };
}
