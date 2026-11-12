import { MutableRefObject, useCallback, useEffect, useRef, useState } from 'react';
import { LogEntry } from '../../types.ts';
import { ProcessedLogEntry } from '../../utils/logEntry.ts';

/** Most rows Select All ticks: the AI analysis takes at most this many logs. */
export const SELECT_ALL_LIMIT = 200;

/**
 * The ticked rows of the Console list, by log id, with Shift-click ranges. The anchor is the index
 * of the last row ticked: rows added above it move it down (shiftAnchor), and a new list clears it.
 * logsRef is the list as last rendered. A positive clearSignal from the parent clears the selection.
 */
export function useLogSelection(logsRef: MutableRefObject<ProcessedLogEntry[]>, clearSignal?: number) {
  const [selectedLogIds, setSelectedLogIds] = useState<Set<number>>(new Set());
  const lastSelectedLogIndexRef = useRef<number | null>(null);

  // Clear selection when signaled from parent (e.g. AI modal closed)
  useEffect(() => {
    if (clearSignal !== undefined && clearSignal > 0) {
      setSelectedLogIds(new Set());
      lastSelectedLogIndexRef.current = null;
    }
  }, [clearSignal]);

  // Multi-select across single or multiple hosts with Shift-click range support
  const toggleSelectLog = useCallback(
    (log: LogEntry, index: number, shiftKey: boolean) => {
      const lastIndex = lastSelectedLogIndexRef.current;

      setSelectedLogIds((prev) => {
        const next = new Set(prev);
        if (shiftKey && lastIndex !== null && lastIndex !== index) {
          const start = Math.min(lastIndex, index);
          const end = Math.max(lastIndex, index);
          const rangeLogs = logsRef.current.slice(start, end + 1);
          rangeLogs.forEach((l) => next.add(l.id));
        } else if (next.has(log.id)) {
          next.delete(log.id);
        } else {
          next.add(log.id);
        }
        return next;
      });

      lastSelectedLogIndexRef.current = index;
    },
    [logsRef],
  );

  /** Moves the range anchor down by the number of rows added above it. */
  const shiftAnchor = useCallback((rowsAdded: number) => {
    if (lastSelectedLogIndexRef.current !== null) lastSelectedLogIndexRef.current += rowsAdded;
  }, []);

  /** Forgets the range anchor, for a list whose rows moved. */
  const clearAnchor = useCallback(() => {
    lastSelectedLogIndexRef.current = null;
  }, []);

  /** Ticks every row in the buffer, up to SELECT_ALL_LIMIT. */
  const selectAll = useCallback(() => {
    setSelectedLogIds(new Set(logsRef.current.slice(0, SELECT_ALL_LIMIT).map((l) => l.id)));
  }, [logsRef]);

  const deselectAll = useCallback(() => {
    setSelectedLogIds(new Set());
    lastSelectedLogIndexRef.current = null;
  }, []);

  /** Ticks exactly these logs. */
  const selectOnly = useCallback((logs: LogEntry[]) => {
    setSelectedLogIds(new Set(logs.map((l) => l.id)));
  }, []);

  /** Unticks logs that left the list (deleted), and forgets the anchor. */
  const forget = useCallback((ids: Iterable<number>) => {
    lastSelectedLogIndexRef.current = null;
    setSelectedLogIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.delete(id);
      return next;
    });
  }, []);

  return { selectedLogIds, toggleSelectLog, shiftAnchor, clearAnchor, selectAll, deselectAll, selectOnly, forget };
}
