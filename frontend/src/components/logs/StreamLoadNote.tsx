import React from 'react';
import { Gauge } from 'lucide-react';
import { NearLimitReason } from '../../utils/nearLimit.ts';

interface StreamLoadNoteProps {
  // Live updates the server skipped for this Console while the note shows
  skipped: number;
  // Why LogShed is near its processing limit (the Navbar's amber dot), or null
  nearLimit: NearLimitReason | null;
  // A search query is set: the browser matches it, so the server's count includes logs it would hide
  searching?: boolean;
}

export const getStreamLoadText = (
  skipped: number,
  nearLimit: NearLimitReason | null,
  searching = false,
): string | null => {
  if (skipped > 0) {
    const updates = skipped === 1 ? 'live update' : 'live updates';
    const scope = searching ? ' (not all of them match your search)' : '';
    const how = searching ? 'search again' : 'search';
    return `High load: ${skipped.toLocaleString('en-US')} ${updates} skipped${scope}. Every log is still stored; ${how} to see them.`;
  }
  if (nearLimit === 'loop' || nearLimit === 'both') {
    return 'LogShed is busy: live updates may arrive late.';
  }
  return null;
};

/** The Console's note, above the log rows, that the live view is not showing every new log as it arrives. */
export const StreamLoadNote: React.FC<StreamLoadNoteProps> = ({ skipped, nearLimit, searching = false }) => {
  const text = getStreamLoadText(skipped, nearLimit, searching);
  if (!text) return null;
  // The count changes with every skip; screen readers get a sentence without it, read once
  const announced = skipped > 0
    ? 'High load: live updates are being skipped. Every log is still stored.'
    : text;
  return (
    <div
      data-testid="stream-load-note"
      className="shrink-0 px-3 py-1.5 bg-warning-950/60 border-b border-warning-800 text-xs text-warning-300 flex items-center gap-2"
    >
      <Gauge className="w-4 h-4 text-warning-400 shrink-0" aria-hidden="true" />
      <span aria-hidden="true">{text}</span>
      <span role="status" className="sr-only">{announced}</span>
    </div>
  );
};
