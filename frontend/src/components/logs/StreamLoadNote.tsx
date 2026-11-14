import React from 'react';
import { Gauge } from 'lucide-react';
import { NearLimitReason } from '../../utils/nearLimit.ts';

interface StreamLoadNoteProps {
  // Live updates the server skipped for this Console while the note shows
  skipped: number;
  // Why LogShed is near its processing limit (the Navbar's amber dot), or null
  nearLimit: NearLimitReason | null;
}

export const getStreamLoadText = (skipped: number, nearLimit: NearLimitReason | null): string | null => {
  if (skipped > 0) {
    const updates = skipped === 1 ? 'live update' : 'live updates';
    return `High load: ${skipped.toLocaleString('en-US')} ${updates} skipped. Every log is still stored; search to see them.`;
  }
  if (nearLimit === 'loop' || nearLimit === 'both') {
    return 'LogShed is busy: live updates may arrive late.';
  }
  return null;
};

/** The Console's note, above the log rows, that the live view is not showing every new log as it arrives. */
export const StreamLoadNote: React.FC<StreamLoadNoteProps> = ({ skipped, nearLimit }) => {
  const text = getStreamLoadText(skipped, nearLimit);
  if (!text) return null;
  return (
    <div
      role="status"
      data-testid="stream-load-note"
      className="shrink-0 px-3 py-1.5 bg-warning-950/60 border-b border-warning-800 text-xs text-warning-300 flex items-center gap-2"
    >
      <Gauge className="w-4 h-4 text-warning-400 shrink-0" aria-hidden="true" />
      <span>{text}</span>
    </div>
  );
};
