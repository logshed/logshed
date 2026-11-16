import { useCallback, useEffect, useMemo, useState } from 'react';
import type React from 'react';
import { readBrowserPref, removeBrowserPref, writeBrowserPref } from '../../utils/browserPrefs.ts';

/** The desktop Console columns that can be resized; the others keep their width. */
export type ResizableColumn = 'time' | 'host' | 'app';
export const RESIZABLE_COLUMNS: ResizableColumn[] = ['time', 'host', 'app'];

export type ColumnWidths = Record<ResizableColumn, number>;

export const DEFAULT_COLUMN_WIDTHS: ColumnWidths = { time: 120, host: 130, app: 130 };

/** Narrowest each column gets: room for its heading (11 px monospace) and the handle beside it. */
export const MIN_COLUMN_WIDTHS: ColumnWidths = { time: 72, host: 72, app: 112 };

/** Widest any column gets, whatever the window. */
export const MAX_COLUMN_WIDTH = 800;

/** Narrowest the message column is left by the resizable columns. */
export const MIN_MESSAGE_WIDTH = 200;

/** One step of the keyboard (Left and Right arrows), in px. */
export const COLUMN_KEY_STEP = 8;

// Checkbox 36 px and severity 65 px, the row's 12 px side padding and 2 px left border; actions 60 px with AI
const FIXED_WIDTH = 36 + 65 + 24 + 2;
const ACTIONS_WIDTH = 60;

const STORAGE_KEY = 'console.columnWidths.v1';

/** The CSS variable that holds each column's width on the box around the header and the rows. */
export const COLUMN_VARS: Record<ResizableColumn, string> = { time: '--col-time', host: '--col-host', app: '--col-app' };

/** Stored widths, each a whole number within its column's limits; null for anything else. */
export function parseStoredWidths(value: unknown): Partial<ColumnWidths> | null {
  if (!value || typeof value !== 'object') return null;
  const out: Partial<ColumnWidths> = {};
  for (const col of RESIZABLE_COLUMNS) {
    const w = (value as Record<string, unknown>)[col];
    if (typeof w === 'number' && Number.isFinite(w) && w >= MIN_COLUMN_WIDTHS[col] && w <= MAX_COLUMN_WIDTH) {
      out[col] = Math.round(w);
    }
  }
  return Object.keys(out).length > 0 ? out : null;
}

/**
 * The widths that fit a header of rowWidth px: wanted widths, each within its own limits, then
 * narrowed from the last column back (app, host, time) to no less than their minimums until the
 * message column keeps MIN_MESSAGE_WIDTH. rowWidth 0 (not measured yet) applies only the limits.
 */
export function fitColumnWidths(wanted: ColumnWidths, rowWidth: number, aiEnabled: boolean): ColumnWidths {
  const fitted = { ...wanted };
  for (const col of RESIZABLE_COLUMNS) {
    fitted[col] = Math.min(MAX_COLUMN_WIDTH, Math.max(MIN_COLUMN_WIDTHS[col], Math.round(fitted[col])));
  }
  if (rowWidth <= 0) return fitted;
  const room = rowWidth - FIXED_WIDTH - (aiEnabled ? ACTIONS_WIDTH : 0) - MIN_MESSAGE_WIDTH;
  let over = RESIZABLE_COLUMNS.reduce((sum, col) => sum + fitted[col], 0) - room;
  for (const col of [...RESIZABLE_COLUMNS].reverse()) {
    if (over <= 0) break;
    const give = Math.min(over, fitted[col] - MIN_COLUMN_WIDTHS[col]);
    fitted[col] -= give;
    over -= give;
  }
  return fitted;
}

/** The widest col can be made now, with the other columns as they are. */
export function maxColumnWidth(col: ResizableColumn, widths: ColumnWidths, rowWidth: number, aiEnabled: boolean): number {
  if (rowWidth <= 0) return MAX_COLUMN_WIDTH;
  const others = RESIZABLE_COLUMNS.filter((c) => c !== col).reduce((sum, c) => sum + widths[c], 0);
  const room = rowWidth - FIXED_WIDTH - (aiEnabled ? ACTIONS_WIDTH : 0) - MIN_MESSAGE_WIDTH - others;
  return Math.max(MIN_COLUMN_WIDTHS[col], Math.min(MAX_COLUMN_WIDTH, room));
}

/**
 * The Console's column widths: remembered by this browser, fitted to the header's width (pass
 * headerRef as the header's ref; it is followed as the window changes), and applied as CSS
 * variables (styleVars) on varsRef's element, the box around the header and the rows, so a change
 * restyles one element and no row re-renders. preview() moves a column during a drag without a
 * React render; commit() keeps the width.
 */
export function useColumnWidths(varsRef: React.RefObject<HTMLElement | null>, aiEnabled: boolean) {
  const [wanted, setWanted] = useState<ColumnWidths>(() => ({
    ...DEFAULT_COLUMN_WIDTHS,
    ...(readBrowserPref(STORAGE_KEY, parseStoredWidths) ?? {}),
  }));
  const [rowWidth, setRowWidth] = useState(0);

  // The header is not rendered on phones, so the observer follows the element as it comes and goes
  const [header, headerRef] = useState<HTMLElement | null>(null);
  useEffect(() => {
    if (!header || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => setRowWidth(Math.round(entries[0].contentRect.width + 24 + 2)));
    observer.observe(header);
    return () => observer.disconnect();
  }, [header]);

  const widths = useMemo(() => fitColumnWidths(wanted, rowWidth, aiEnabled), [wanted, rowWidth, aiEnabled]);

  const styleVars = useMemo(
    () =>
      Object.fromEntries(RESIZABLE_COLUMNS.map((col) => [COLUMN_VARS[col], `${widths[col]}px`])) as React.CSSProperties,
    [widths],
  );

  const bounds = useCallback(
    (col: ResizableColumn) => ({ min: MIN_COLUMN_WIDTHS[col], max: maxColumnWidth(col, widths, rowWidth, aiEnabled) }),
    [widths, rowWidth, aiEnabled],
  );

  const clampTo = useCallback(
    (col: ResizableColumn, px: number) => {
      const { min, max } = bounds(col);
      return Math.min(max, Math.max(min, Math.round(px)));
    },
    [bounds],
  );

  /** Shows col at px (clamped) without a React render; returns the width applied. */
  const preview = useCallback(
    (col: ResizableColumn, px: number) => {
      const w = clampTo(col, px);
      varsRef.current?.style.setProperty(COLUMN_VARS[col], `${w}px`);
      return w;
    },
    [clampTo, varsRef],
  );

  const save = (next: ColumnWidths) => {
    if (RESIZABLE_COLUMNS.every((c) => next[c] === DEFAULT_COLUMN_WIDTHS[c])) removeBrowserPref(STORAGE_KEY);
    else writeBrowserPref(STORAGE_KEY, next);
  };

  /** Keeps col at px (clamped) and remembers it. */
  const commit = useCallback(
    (col: ResizableColumn, px: number) => {
      const w = clampTo(col, px);
      setWanted((prev) => {
        const next = { ...prev, [col]: w };
        save(next);
        return next;
      });
    },
    [clampTo],
  );

  /** Puts one column, or every column, back to its default width. */
  const reset = useCallback((col?: ResizableColumn) => {
    setWanted((prev) => {
      const next = col ? { ...prev, [col]: DEFAULT_COLUMN_WIDTHS[col] } : { ...DEFAULT_COLUMN_WIDTHS };
      save(next);
      return next;
    });
  }, []);

  const changed = RESIZABLE_COLUMNS.some((c) => wanted[c] !== DEFAULT_COLUMN_WIDTHS[c]);

  return { headerRef, widths, styleVars, bounds, preview, commit, reset, changed };
}
