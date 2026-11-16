import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { fetchLogHistogram, LogHistogram, LogHistogramBucket } from '../../api/logs.ts';
import { LogFilterParams } from '../../types.ts';
import { DAY_MS, buildTimeTicks, formatTimePoint, formatTimeTick } from '../../utils/timeAxis.ts';

// Filter edits wait as long as the list's search does, so typing sends one request
export const HISTOGRAM_DEBOUNCE_MS = 300;
// While the Console follows new logs, the counts are fetched again this long after each answer
export const HISTOGRAM_REFRESH_MS = 10_000;

const GROUPS = [
  { key: 'error', label: 'Error and worse', color: 'var(--color-danger-500)', swatch: 'bg-danger-500' },
  { key: 'warning', label: 'Warning', color: 'var(--color-warning-500)', swatch: 'bg-warning-500' },
  { key: 'info', label: 'Notice and info', color: 'var(--color-accent-600)', swatch: 'bg-accent-600' },
  { key: 'debug', label: 'Debug', color: 'var(--color-neutral-500)', swatch: 'bg-neutral-500' },
] as const;

type GroupKey = (typeof GROUPS)[number]['key'];

interface VolumeBar {
  mid: number;
  start: number;
  end: number;
  total: number;
  error: number;
  warning: number;
  info: number;
  debug: number;
}

interface VolumeHistogramProps {
  filters: LogFilterParams;
  /** The server's time zone: bars and labels follow its clock. */
  timeZone?: string;
  isMobile: boolean;
  /** Called with a bar's span (UTC ISO times) when it is clicked. */
  onZoom: (from: string, to: string) => void;
}

const toBar = (b: LogHistogramBucket): VolumeBar => {
  const start = Date.parse(b.start);
  const end = Date.parse(b.end);
  return { mid: (start + end) / 2, start, end, total: b.error + b.warning + b.info + b.debug, error: b.error, warning: b.warning, info: b.info, debug: b.debug };
};

/** A bar's span for its tooltip: '3/14 09:05 - 09:06', with the end's date only when it differs. */
function spanLabel(bar: VolumeBar, timeZone?: string): string {
  const from = formatTimePoint(bar.start, timeZone);
  const to = formatTimePoint(bar.end, timeZone);
  const [fromDay] = from.split(' ');
  const [toDay, toTime] = to.split(' ');
  return `${from} - ${fromDay === toDay ? toTime : to}`;
}

/** A bar's tooltip: its span and total, then each severity group with logs, worst first. */
const BarTooltip: React.FC<{ bar: VolumeBar; timeZone?: string }> = ({ bar, timeZone }) => (
  <div className="rounded-lg border border-line-strong bg-surface px-2.5 py-1.5 font-mono text-[11px] text-fg shadow-lg">
    <div className="text-fg-strong">
      {spanLabel(bar, timeZone)}: {bar.total.toLocaleString()}
    </div>
    {GROUPS.filter((g) => bar[g.key] > 0).map((g) => (
      <div key={g.key} className="flex items-center gap-1.5 text-fg-soft">
        <span aria-hidden="true" className={`inline-block size-2 rounded-xs ${g.swatch}`} />
        {g.label}: {bar[g.key].toLocaleString()}
      </div>
    ))}
  </div>
);

/** Only the parts of the filters the counts depend on, as a key that changes when they do. */
const filterKey = (f: LogFilterParams) =>
  JSON.stringify([f.query ?? '', f.severity_max ?? null, f.sources ?? f.source ?? [], f.apps ?? f.app_name ?? [], f.from ?? '', f.to ?? '']);

/**
 * A small bar chart of how many logs the Console's filters select per time bucket, stacked by
 * severity group, over the Console's time range (the last hour on All Time). Clicking a bar
 * zooms the Console to it. While the Console follows new logs the counts are fetched again
 * every few seconds, never from the browser's own buffer, which drops logs under load.
 */
export const VolumeHistogram: React.FC<VolumeHistogramProps> = ({ filters, timeZone, isMobile, onZoom }) => {
  const [data, setData] = useState<LogHistogram | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = filterKey(filters);
  const filtersRef = useRef(filters);
  filtersRef.current = filters;
  const live = !filters.to;

  useEffect(() => {
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    // Set when a fetch was skipped because the tab was hidden
    let skipped = false;

    const schedule = (ms: number) => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(load, ms);
    };
    const load = async () => {
      timer = null;
      if (document.visibilityState === 'hidden') {
        skipped = true;
        return;
      }
      skipped = false;
      controller?.abort();
      controller = new AbortController();
      try {
        const result = await fetchLogHistogram(filtersRef.current, { signal: controller.signal });
        if (cancelled) return;
        setData(result);
        setError(null);
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === 'AbortError')) return;
        setError(err instanceof Error ? err.message : 'request failed');
      }
      if (!cancelled && live) schedule(HISTOGRAM_REFRESH_MS);
    };
    // Back on the tab: fetch at once if a fetch was skipped while it was hidden
    const onVisible = () => {
      if (document.visibilityState === 'visible' && skipped) schedule(0);
    };

    schedule(HISTOGRAM_DEBOUNCE_MS);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      controller?.abort();
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [key, live]);

  const bars = useMemo(() => (data ? data.buckets.map(toBar) : []), [data]);
  const total = bars.reduce((sum, b) => sum + b.total, 0);
  const rangeFrom = data ? Date.parse(data.range_from) : 0;
  const rangeTo = data ? Date.parse(data.range_to) : 0;
  const { ticks, stepMs } = useMemo(
    () => buildTimeTicks(rangeFrom, rangeTo, timeZone, { maxTicks: isMobile ? 4 : 8 }),
    [rangeFrom, rangeTo, timeZone, isMobile],
  );
  const spansDays = rangeTo - rangeFrom > DAY_MS;
  const halfBucket = data ? (data.bucket_seconds * 1000) / 2 : 0;
  const caption = !filters.from && !filters.to ? 'Log volume, last hour' : 'Log volume';
  const busiest = bars.reduce<VolumeBar | null>((top, b) => (!top || b.total > top.total ? b : top), null);
  const summary = data
    ? `${caption}: ${total.toLocaleString()} logs from ${formatTimePoint(rangeFrom, timeZone)} to ${formatTimePoint(rangeTo, timeZone)}` +
      (busiest && busiest.total > 0 ? `, most between ${spanLabel(busiest, timeZone)} (${busiest.total.toLocaleString()})` : '')
    : `${caption}: loading`;

  return (
    // Its own layer, so a bar's tooltip shows over the log list below instead of under it
    <div className="relative z-10 shrink-0 bg-surface border-b border-line px-3 pt-1 pb-0.5 select-none" data-testid="volume-histogram">
      <div className="flex items-center justify-between gap-2 text-[11px] font-mono text-fg-muted">
        <span>
          {caption}
          {data && <span className="text-fg">: {total.toLocaleString()}</span>}
        </span>
        {error ? (
          <span role="status" className="text-danger-300 truncate">Could not load the counts: {error}</span>
        ) : (
          !isMobile && <span>Click a bar to zoom in</span>
        )}
      </div>
      <div className={`relative ${isMobile ? 'h-14' : 'h-20'}`} role="img" aria-label={summary}>
        {data && (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={bars} margin={{ top: 4, right: 4, left: 0, bottom: 0 }} barCategoryGap={1}>
              <XAxis
                dataKey="mid"
                type="number"
                scale="time"
                domain={[rangeFrom - halfBucket, rangeTo + halfBucket]}
                ticks={ticks}
                tickFormatter={(ms: number) => formatTimeTick(ms, stepMs, timeZone, spansDays)}
                stroke="var(--color-fg-subtle)"
                tick={{ fontSize: 10 }}
                height={16}
                tickLine={false}
              />
              <YAxis hide domain={[0, 'dataMax']} />
              <Tooltip
                cursor={{ fill: 'var(--color-raised)' }}
                wrapperStyle={{ zIndex: 30 }}
                content={({ active, payload }) => {
                  const bar = active ? (payload?.[0]?.payload as VolumeBar | undefined) : undefined;
                  return bar ? <BarTooltip bar={bar} timeZone={timeZone} /> : null;
                }}
              />
              {GROUPS.map((g) => (
                <Bar
                  key={g.key}
                  dataKey={g.key as GroupKey}
                  stackId="volume"
                  fill={g.color}
                  isAnimationActive={false}
                  cursor="pointer"
                  onClick={(entry: { payload?: VolumeBar }) => {
                    const bar = entry?.payload;
                    if (bar) onZoom(new Date(bar.start).toISOString(), new Date(bar.end).toISOString());
                  }}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        )}
        {data && total === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-[11px] font-mono text-fg-muted pointer-events-none">
            No logs in this range
          </div>
        )}
      </div>
    </div>
  );
};
