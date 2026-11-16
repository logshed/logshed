import React, { useRef } from 'react';
import { COLUMN_KEY_STEP } from './useColumnWidths.ts';

interface ColumnResizeHandleProps {
  /** The column's heading, for the handle's name ("Resize Timestamp column"). */
  label: string;
  width: number;
  min: number;
  max: number;
  /** Shows a width during a drag (without a React render); returns the width applied. */
  onPreview: (px: number) => number;
  /** Keeps a width: the end of a drag, or a key press. */
  onCommit: (px: number) => void;
  /** Double-click or Enter: back to the column's default width. */
  onReset: () => void;
}

/**
 * The drag handle at the right edge of a resizable Console heading, inside it, so its line stands
 * 6 px clear of the next column's heading text: a focusable vertical separator
 * (the WAI-ARIA window splitter pattern) whose value is the column's width in px. Drag it, or use
 * Left and Right (8 px a press), Home and End (narrowest and widest); Enter or a double-click resets it.
 */
export const ColumnResizeHandle: React.FC<ColumnResizeHandleProps> = ({ label, width, min, max, onPreview, onCommit, onReset }) => {
  const drag = useRef<{ startX: number; startWidth: number; last: number } | null>(null);

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { startX: e.clientX, startWidth: width, last: width };
  };

  /** Keeps the width the drag reached. Also runs when the release was lost outside the window. */
  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    if (d.last !== d.startWidth) onCommit(d.last);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    // No button held: the release happened where this handle did not hear it (Firefox, outside the
    // window, reports such moves at x 0), so the drag ends where it last was
    if ((e.buttons & 1) === 0) {
      endDrag(e);
      return;
    }
    d.last = onPreview(d.startWidth + e.clientX - d.startX);
    e.currentTarget.setAttribute('aria-valuenow', String(d.last));
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const next =
      e.key === 'ArrowLeft' ? width - COLUMN_KEY_STEP
      : e.key === 'ArrowRight' ? width + COLUMN_KEY_STEP
      : e.key === 'Home' ? min
      : e.key === 'End' ? max
      : null;
    if (next !== null) {
      e.preventDefault();
      // The log list must not take these keys as a cursor move
      e.stopPropagation();
      onCommit(next);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      onReset();
    }
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={`Resize ${label} column`}
      aria-valuenow={width}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      title="Drag to resize; double-click to reset"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onLostPointerCapture={endDrag}
      onDoubleClick={onReset}
      onKeyDown={handleKeyDown}
      className="group/handle absolute right-0 top-0 bottom-0 z-10 flex w-3 cursor-col-resize touch-none justify-center outline-hidden"
    >
      <span className="w-px bg-line group-hover/handle:w-0.5 group-hover/handle:bg-accent-500 group-focus-visible/handle:w-0.5 group-focus-visible/handle:bg-accent-400" />
    </div>
  );
};
