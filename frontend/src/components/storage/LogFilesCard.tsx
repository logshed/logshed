import React, { useState } from 'react';
import { CalendarDays } from 'lucide-react';
import { LogFileItem } from '../../types.ts';
import { formatBytes, formatLocalTimestamp } from '../../utils/formatters.ts';
import { useServerTimezone } from '../../utils/hooks.ts';
import { Badge, BadgeTone } from '../common/Badge.tsx';
import { Button } from '../common/Button.tsx';
import { Card, CardHeader } from '../common/Card.tsx';
import { TableHead } from '../common/Table.tsx';

/** Rows shown before "Show all". */
export const LOG_FILES_SHOWN = 7;

const STATES: Record<string, { label: string; tone: BadgeTone; title: string }> = {
  open: { label: 'open', tone: 'accent', title: 'Receiving logs, or its day is over and it is still being indexed' },
  sealed: { label: 'sealed', tone: 'neutral', title: 'Its day is over: the search index is compacted and its counts are stored' },
  legacy: { label: 'before 1.4.0', tone: 'warning', title: 'Logs stored before the upgrade, inside logs.db; retention removes them as they age' },
};

function stateBadge(state: string): React.ReactNode {
  const look = STATES[state];
  return (
    <Badge tone={look?.tone ?? 'neutral'} title={look?.title}>
      {look?.label ?? state}
    </Badge>
  );
}

interface LogFilesCardProps {
  files: LogFileItem[];
  retentionDays?: number | null;
}

/** The Storage page's list of day files: one per UTC day, deleted whole by retention. */
export const LogFilesCard: React.FC<LogFilesCardProps> = ({ files, retentionDays }) => {
  const serverTimezone = useServerTimezone();
  const [showAll, setShowAll] = useState<boolean>(false);
  if (files.length === 0) return null;

  const days = files.filter((f) => f.state !== 'legacy');
  const legacy = files.find((f) => f.state === 'legacy');
  const shown = showAll ? days : days.slice(0, LOG_FILES_SHOWN);
  const expires = (f: LogFileItem) => (f.expires_at ? formatLocalTimestamp(f.expires_at, false, serverTimezone) : '');

  return (
    <Card data-testid="log-files-card">
      <CardHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            Log Files
            <Badge code>
              {days.length} {days.length === 1 ? 'day' : 'days'}
            </Badge>
          </span>
        }
        icon={<CalendarDays />}
        description={
          `Logs are stored in one file per UTC day and deleted a whole day at a time, once the whole day is older than ` +
          `the retention${retentionDays ? ` of ${retentionDays} ${retentionDays === 1 ? 'day' : 'days'}` : ''}, ` +
          `so a log can be kept up to a day longer. Expiry times are in the server's time zone.`
        }
      />
      {/* Focusable, so the table can be scrolled by keyboard on a screen too narrow for it */}
      <div className="overflow-x-auto border border-line rounded-lg" tabIndex={0} role="region" aria-label="Log files table">
        <table className="w-full text-left border-collapse text-xs" aria-label="Log files">
          <TableHead
            columns={[
              { label: 'Day (UTC)' },
              { label: 'Logs', align: 'right' },
              { label: 'Size', align: 'right', className: 'hidden sm:table-cell' },
              { label: 'State', className: 'hidden sm:table-cell' },
              { label: 'Expires' },
            ]}
          />
          <tbody className="divide-y divide-line-subtle text-fg">
            {shown.map((f) => (
              <tr key={f.day}>
                <td className="py-2 px-4 font-mono text-fg-strong whitespace-nowrap">
                  {f.day}
                  {/* On a phone the state sits under the day */}
                  <span className="block sm:hidden mt-0.5">{stateBadge(f.state)}</span>
                </td>
                <td className="py-2 px-4 font-mono text-right whitespace-nowrap">{f.logs.toLocaleString()}</td>
                <td className="py-2 px-4 font-mono text-right whitespace-nowrap hidden sm:table-cell">{formatBytes(f.size_bytes ?? 0)}</td>
                <td className="py-2 px-4 hidden sm:table-cell">{stateBadge(f.state)}</td>
                <td className="py-2 px-4 font-mono text-fg-muted whitespace-nowrap">{expires(f)}</td>
              </tr>
            ))}
            {legacy && (
              <tr data-testid="legacy-row">
                <td className="py-2 px-4 text-fg-strong whitespace-nowrap">
                  Before 1.4.0
                  <span className="block sm:hidden mt-0.5">{stateBadge('legacy')}</span>
                </td>
                <td className="py-2 px-4 font-mono text-right whitespace-nowrap">{legacy.logs.toLocaleString()}</td>
                <td className="py-2 px-4 text-fg-muted text-right whitespace-nowrap hidden sm:table-cell">in logs.db</td>
                <td className="py-2 px-4 hidden sm:table-cell">{stateBadge('legacy')}</td>
                <td className="py-2 px-4 font-mono text-fg-muted whitespace-nowrap">
                  {legacy.expires_at ? `all gone by ${expires(legacy)}` : ''}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {days.length > LOG_FILES_SHOWN && (
        <div>
          <Button variant="secondary" size="sm" onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
            {showAll ? 'Show fewer' : `Show all ${days.length}`}
          </Button>
        </div>
      )}
    </Card>
  );
};

export default LogFilesCard;
