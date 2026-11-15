import React, { useEffect, useState } from 'react';
import { BarChart3, Filter, ShieldOff } from 'lucide-react';
import { fetchTopSources } from '../../api/system.ts';
import { SourceCount, TopSources } from '../../types.ts';
import { formatLocalTimestamp } from '../../utils/formatters.ts';
import { usePolling, useServerTimezone } from '../../utils/hooks.ts';
import { Card, CardHeader } from '../common/Card.tsx';
import { IconButton } from '../common/Button.tsx';

const CreateDropRuleModal = React.lazy(() =>
  import('../alerts/CreateDropRuleModal.tsx').then((m) => ({ default: m.CreateDropRuleModal }))
);

// The server works the figures out every 5 minutes; a minute's wait shows each new set soon enough
const REFRESH_MS = 60_000;

type Kind = 'host' | 'app';

/** Opens the Console on the last 24 hours of one host or app (the same URL hand-off as View logs). */
function filterInConsole(kind: Kind, name: string) {
  const key = kind === 'host' ? 'source' : 'app';
  window.history.pushState(null, '', `/?${key}=${encodeURIComponent(name)}&time=24h`);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

function share(count: number, total: number): string {
  if (!total) return '0%';
  const pct = (count / total) * 100;
  return pct >= 10 || pct === 0 ? `${Math.round(pct)}%` : `${pct.toFixed(1)}%`;
}

const SourceList: React.FC<{
  kind: Kind;
  title: string;
  rows: SourceCount[];
  total: number;
  onDropRule: (kind: Kind, name: string) => void;
}> = ({ kind, title, rows, total, onDropRule }) => (
  <div className="min-w-0">
    <h4 className="text-[11px] font-semibold uppercase tracking-wide text-fg-muted mb-2">{title}</h4>
    {rows.length === 0 ? (
      <p className="text-xs text-fg-muted">None.</p>
    ) : (
      <ol className="space-y-2.5" aria-label={title}>
        {rows.map((row) => (
          <li key={row.name} className="text-xs" data-testid={`top-${kind}`}>
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-fg truncate min-w-0" title={row.name}>
                {row.name}
              </span>
              <span className="flex items-center gap-1 shrink-0">
                <span className="font-mono text-fg-soft tabular-nums">{row.count.toLocaleString()}</span>
                <span className="text-fg-muted tabular-nums w-11 text-right">{share(row.count, total)}</span>
                <IconButton
                  size="sm"
                  onClick={() => filterInConsole(kind, row.name)}
                  title="Filter in Console"
                  label={`Show the last 24 hours of ${row.name} in the Console`}
                >
                  <Filter className="w-3.5 h-3.5" />
                </IconButton>
                <IconButton
                  size="sm"
                  onClick={() => onDropRule(kind, row.name)}
                  title="Create Drop Rule"
                  label={`Create a drop rule for ${row.name}`}
                >
                  <ShieldOff className="w-3.5 h-3.5" />
                </IconButton>
              </span>
            </div>
            <div className="mt-1 h-1.5 bg-canvas rounded-full overflow-hidden border border-line" aria-hidden="true">
              <div className="h-full bg-accent-500" style={{ width: `${total ? (row.count / total) * 100 : 0}%` }} />
            </div>
          </li>
        ))}
      </ol>
    )}
  </div>
);

/**
 * The five hosts and five apps that sent the most logs in the last 24 hours, with their share of
 * all logs, and buttons to see them in the Console or start a drop rule for them.
 */
export const TopSourcesCard: React.FC = () => {
  const [data, setData] = useState<TopSources | null>(null);
  const [failed, setFailed] = useState(false);
  const [dropRule, setDropRule] = useState<{ kind: Kind; name: string } | null>(null);
  const serverTimezone = useServerTimezone();

  const load = () => {
    fetchTopSources()
      .then((res) => {
        setData(res);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  };

  useEffect(() => {
    load();
  }, []);
  usePolling(load, REFRESH_MS);

  const asOf = data?.as_of ? formatLocalTimestamp(data.as_of, false, serverTimezone) : null;

  let body: React.ReactNode;
  if (failed && !data) {
    body = <p className="text-xs text-danger-400">The top sources could not be loaded.</p>;
  } else if (!data) {
    body = <p className="text-xs text-fg-muted">Loading...</p>;
  } else if (!data.as_of) {
    body = <p className="text-xs text-fg-muted">LogShed is working out the figures; they appear a minute or so after it starts.</p>;
  } else if (data.total === 0) {
    body = <p className="text-xs text-fg-muted">No logs in the last 24 hours.</p>;
  } else {
    body = (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <SourceList kind="host" title="Hosts" rows={data.hosts} total={data.total} onDropRule={(kind, name) => setDropRule({ kind, name })} />
        <SourceList kind="app" title="Apps" rows={data.apps} total={data.total} onDropRule={(kind, name) => setDropRule({ kind, name })} />
      </div>
    );
  }

  return (
    <Card data-testid="top-sources-card">
      <CardHeader
        icon={<BarChart3 />}
        title="Top Sources (Last 24h)"
        description={
          data?.as_of
            ? `${data.total.toLocaleString()} logs since ${formatLocalTimestamp(data.since, false, serverTimezone)}, by each log's own time. Updated every 5 minutes, last at ${asOf}.`
            : 'The hosts and apps that sent the most logs, updated every 5 minutes.'
        }
        className="mb-4"
      />
      {body}
      {dropRule && (
        <React.Suspense fallback={null}>
          <CreateDropRuleModal
            isOpen
            onClose={() => setDropRule(null)}
            initialSource={dropRule.kind === 'host' ? dropRule.name : ''}
            initialApp={dropRule.kind === 'app' ? dropRule.name : ''}
          />
        </React.Suspense>
      )}
    </Card>
  );
};
