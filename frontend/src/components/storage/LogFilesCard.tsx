import React, { useState } from 'react';
import { CalendarDays } from 'lucide-react';
import { LogFileItem } from '../../types.ts';
import { formatBytes, formatLocalTimestamp } from '../../utils/formatters.ts';
import { useMediaQuery, useServerTimezone } from '../../utils/hooks.ts';
import { Badge, BadgeTone } from '../common/Badge.tsx';
import { Button, DisclosureButton } from '../common/Button.tsx';
import { Card, CardHeader } from '../common/Card.tsx';
import { TableHead } from '../common/Table.tsx';

/** Rows shown, once the list is open, before "Show all". */
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

/**
 * The Storage page's list of day files: one per UTC day, deleted whole by retention. It starts closed,
 * as a summary (the day files' size, when the oldest expires, the logs left from before 1.4.0), so a
 * long retention does not make the page long; the list opens under it.
 */
export const LogFilesCard: React.FC<LogFilesCardProps> = ({ files, retentionDays }) => {
  const serverTimezone = useServerTimezone();
  const [open, setOpen] = useState<boolean>(false);
  const [showAll, setShowAll] = useState<boolean>(false);
  // Below md the rows are stacked, as the other tables do on a phone (API tokens, Docker hosts)
  const isMobile = useMediaQuery('(max-width: 767px)');
  if (files.length === 0) return null;

  const days = files.filter((f) => f.state !== 'legacy');
  const legacy = files.find((f) => f.state === 'legacy');
  const shown = showAll ? days : days.slice(0, LOG_FILES_SHOWN);
  const expires = (f: LogFileItem) => (f.expires_at ? formatLocalTimestamp(f.expires_at, false, serverTimezone) : '');
  const oldest = days[days.length - 1];
  const totalBytes = days.reduce((sum, f) => sum + (f.size_bytes ?? 0), 0);

  return (
    <Card data-testid="log-files-card">
      <CardHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            Log Files
            {days.length > 0 && (
              <Badge code>
                {days.length} {days.length === 1 ? 'day' : 'days'}
              </Badge>
            )}
          </span>
        }
        icon={<CalendarDays />}
        description={
          `Logs are stored in one file per UTC day and deleted a whole day at a time, once the whole day is older than ` +
          `the retention${retentionDays ? ` of ${retentionDays} ${retentionDays === 1 ? 'day' : 'days'}` : ''}, ` +
          `so a log can be kept up to a day longer. Expiry times are in the server's time zone.`
        }
      />
      <div className="space-y-1 text-xs text-fg-muted" data-testid="log-files-summary">
        {oldest && (
          <p>
            <span className="font-mono text-fg-soft">{formatBytes(totalBytes)}</span> in day files.{' '}
            {oldest.expires_at && (
              <>
                The oldest day, <span className="font-mono text-fg-soft">{oldest.day}</span>, expires at{' '}
                <span className="font-mono text-fg-soft">{expires(oldest)}</span>.
              </>
            )}
          </p>
        )}
        {legacy && (
          <p>
            Logs from before 1.4.0: <span className="font-mono text-fg-soft">{legacy.logs.toLocaleString()}</span> in
            logs.db
            {legacy.expires_at && (
              <>
                , all gone by <span className="font-mono text-fg-soft">{expires(legacy)}</span>
              </>
            )}
            .
          </p>
        )}
      </div>
      {/* The text size and colour come from here: a component's className is for layout only */}
      <div className="text-xs text-fg-soft">
        <DisclosureButton open={open} onToggle={() => setOpen((v) => !v)} className="max-md:min-h-8">
          {open ? 'Hide the day files' : 'Show the day files'}
        </DisclosureButton>
      </div>
      {open && (isMobile ? (
        <div className="border border-line rounded-lg divide-y divide-line-subtle text-xs" role="list" aria-label="Log files">
          {shown.map((f) => (
            <div key={f.day} className="p-3 space-y-1.5" role="listitem">
              <div className="flex items-center justify-between gap-3">
                <span className="font-mono text-fg-strong">{f.day}</span>
                {stateBadge(f.state)}
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-fg-muted">
                <span>
                  Logs: <span className="font-mono text-fg-soft">{f.logs.toLocaleString()}</span>
                </span>
                <span>
                  Size: <span className="font-mono text-fg-soft">{formatBytes(f.size_bytes ?? 0)}</span>
                </span>
                <span>
                  Expires: <span className="font-mono text-fg-soft">{expires(f)}</span>
                </span>
              </div>
            </div>
          ))}
          {legacy && (
            <div className="p-3 space-y-1.5" role="listitem" data-testid="legacy-row">
              <div className="flex items-center justify-between gap-3">
                <span className="text-fg-strong">Before 1.4.0</span>
                {stateBadge('legacy')}
              </div>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-fg-muted">
                <span>
                  Logs: <span className="font-mono text-fg-soft">{legacy.logs.toLocaleString()}</span>
                </span>
                <span>in logs.db</span>
                {legacy.expires_at && (
                  <span>
                    All gone by: <span className="font-mono text-fg-soft">{expires(legacy)}</span>
                  </span>
                )}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="overflow-x-auto border border-line rounded-lg">
          <table className="w-full text-left border-collapse text-xs" aria-label="Log files">
            <TableHead
              columns={[
                { label: 'Day (UTC)' },
                { label: 'Logs', align: 'right' },
                { label: 'Size', align: 'right' },
                { label: 'State' },
                { label: 'Expires' },
              ]}
            />
            <tbody className="divide-y divide-line-subtle text-fg">
              {shown.map((f) => (
                <tr key={f.day}>
                  <td className="py-2 px-4 font-mono text-fg-strong whitespace-nowrap">{f.day}</td>
                  <td className="py-2 px-4 font-mono text-right whitespace-nowrap">{f.logs.toLocaleString()}</td>
                  <td className="py-2 px-4 font-mono text-right whitespace-nowrap">{formatBytes(f.size_bytes ?? 0)}</td>
                  <td className="py-2 px-4">{stateBadge(f.state)}</td>
                  <td className="py-2 px-4 font-mono text-fg-muted whitespace-nowrap">{expires(f)}</td>
                </tr>
              ))}
              {legacy && (
                <tr data-testid="legacy-row">
                  <td className="py-2 px-4 text-fg-strong whitespace-nowrap">Before 1.4.0</td>
                  <td className="py-2 px-4 font-mono text-right whitespace-nowrap">{legacy.logs.toLocaleString()}</td>
                  <td className="py-2 px-4 text-fg-muted text-right whitespace-nowrap">in logs.db</td>
                  <td className="py-2 px-4">{stateBadge('legacy')}</td>
                  <td className="py-2 px-4 font-mono text-fg-muted whitespace-nowrap">
                    {legacy.expires_at ? `all gone by ${expires(legacy)}` : ''}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ))}
      {open && days.length > LOG_FILES_SHOWN && (
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
