import React from 'react';
import { formatAgo, formatLocalTimestamp } from '../../utils/formatters.ts';

/**
 * When a source last sent a log, as "12s ago" with the full time (server time zone) on hover,
 * or "Not seen" when no log has arrived from it since LogShed began recording this (1.4.0).
 */
export const LastSeen: React.FC<{ at?: string | null; now: number; timeZone?: string }> = ({ at, now, timeZone }) =>
  at ? (
    <span className="whitespace-nowrap" title={`Last log received ${formatLocalTimestamp(at, true, timeZone)}`}>
      {formatAgo(at, now)}
    </span>
  ) : (
    <span className="whitespace-nowrap text-fg-muted" title="No log received from this source since LogShed 1.4.0">
      Not seen
    </span>
  );
