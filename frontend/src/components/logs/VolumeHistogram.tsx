import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, ZoomOut } from 'lucide-react';
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { fetchLogHistogram, LogHistogram, LogHistogramBucket } from '../../api/logs.ts';
import { ApiError } from '../../api/client.ts';
import { LogFilterParams } from '../../types.ts';
import { DAY_MS, buildTimeTicks, formatTimePoint, formatTimeTick } from '../../utils/timeAxis.ts';
import { readBrowserPref, writeBrowserPref } from '../../utils/browserPrefs.ts';
import { openDialogCount } from '../../utils/dialogStack.ts';

// Filter edits wait as long as the list's search does, so typing sends one request
export const HISTOGRAM_DEBOUNCE_MS = 300;
// While the Console follows new logs, the counts are fetched again this long after each answer:
// a sixth of a bar, between these bounds, so a 24-hour chart of hour bars on a busy day is not
// recounted every 10 s (about 1.8 s of the server's time for an 8M-row day)
export const HISTOGRAM_REFRESH_MS = 10_000;
export const HISTOGRAM_REFRESH_MAX_MS = 60_000;

/** How long after an answer the counts are fetched again, for bars of bucketSeconds. */
export function refreshDelayMs(bucketSeconds: number | undefined): number {
  if (!bucketSeconds) return HISTOGRAM_REFRESH_MS;
  return Math.min(HISTOGRAM_REFRESH_MAX_MS, Math.max(HISTOGRAM_REFRESH_MS, (bucketSeconds * 1000) / 6));
}

const GROUPS = [
  { key: 'error', label: 'Error and worse', color: 'var(--color-danger-500)', swatch: 'bg-danger-500' },
  { key: 'warning', label: 'Warning', color: 'var(--color-warning-500)', swatch: 'bg-warning-500' },
  { key: 'info', label: 'Notice and info', color: 'var(--color-accent-600)', swatch: 'bg-accent-600' },
  { key: 'debug', label: 'Debug', color: 'var(--color-neutral-500)', swatch: 'bg-neutral-500' },
] as const;

type GroupKey = (typeof GROUPS)[number]['key'];

export interface VolumeBar {
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
  /** True when the user has zoomed in via the chart and can step back. */
  canZoomOut?: boolean;
  /** Restores the previous time range without touching other filters. */
  onZoomOut?: () => void;
}

/**
 * A bucket as a bar. Bars stand at their bucket's middle, and the first and last bucket (cut to
 * the range) at the middle of a whole one, so every bar is spaced, and so drawn, the same width.
 */
const toBar = (b: LogHistogramBucket, i: number, all: LogHistogramBucket[], bucketMs: number): VolumeBar => {
  const start = Date.parse(b.start);
  const end = Date.parse(b.end);
  let mid = (start + end) / 2;
  if (i === 0 && all.length > 1) mid = end - bucketMs / 2;
  else if (i === all.length - 1 && all.length > 1) mid = start + bucketMs / 2;
  return { mid, start, end, total: b.error + b.warning + b.info + b.debug, error: b.error, warning: b.warning, info: b.info, debug: b.debug };
};

/**
 * A bar's span for its tooltip: '14 Mar 09:05 - 09:06', with the end's date only when it differs,
 * and seconds for bars under a minute.
 */
export function spanLabel(bar: VolumeBar, timeZone?: string, withSeconds = false): string {
  const from = formatTimePoint(bar.start, timeZone, withSeconds);
  const to = formatTimePoint(bar.end, timeZone, withSeconds);
  // The time is the last word; the date before it ('14 Mar') has a space of its own
  const fromDay = from.slice(0, from.lastIndexOf(' '));
  const toDay = to.slice(0, to.lastIndexOf(' '));
  const toTime = to.slice(to.lastIndexOf(' ') + 1);
  return `${from} - ${fromDay === toDay ? toTime : to}`;
}

/** A bar's tooltip: its span and total, then each severity group with logs, worst first. */
const BarTooltip: React.FC<{ bar: VolumeBar; timeZone?: string; withSeconds: boolean; zoomHint?: string }> = ({
  bar,
  timeZone,
  withSeconds,
  zoomHint,
}) => (
  <div className="rounded-lg border border-line-strong bg-surface px-2.5 py-1.5 text-left font-mono text-[11px] text-fg shadow-lg">
    <div className="text-fg-strong">
      {spanLabel(bar, timeZone, withSeconds)}: {bar.total.toLocaleString()}
    </div>
    {GROUPS.filter((g) => bar[g.key] > 0).map((g) => (
      <div key={g.key} className="flex items-center gap-1.5 text-fg-soft">
        <span aria-hidden="true" className={`inline-block size-2 rounded-xs ${g.swatch}`} />
        {g.label}: {bar[g.key].toLocaleString()}
      </div>
    ))}
    {zoomHint && <div className="mt-0.5 text-accent-300">{zoomHint}</div>}
  </div>
);

// The tapped bar's colours over the dark band: about half as bright as the others
const PINNED_OPACITY = 0.45;

// How far a finger may move between down and up and still count as a tap on a bar
const TAP_SLOP_PX = 10;

const CHART_MARGIN = { top: 4, right: 4, left: 0, bottom: 0 };
// The time axis under the bars
const AXIS_HEIGHT = 16;

// The bars' plot height in px (the chart's 80 or 56 px less the top margin and the time axis)
const PLOT_HEIGHT = { desktop: 80 - CHART_MARGIN.top - AXIS_HEIGHT, mobile: 56 - CHART_MARGIN.top - AXIS_HEIGHT };
// A severity with any logs is drawn at least this tall (about 3 px of colour inside the 1 px lines between
// stacked parts), so one error next to 50,000 logs still shows
export const MIN_SEGMENT_PX = 5;

export type VolumeScale = 'linear' | 'log';
// The chart's scale, remembered by this browser
export const SCALE_PREF = 'console.histogram.scale.v1';
const parseScale = (v: unknown): VolumeScale | null => (v === 'linear' || v === 'log' ? v : null);

/** A bar's drawn height per severity group (in the chart's units); the bar itself keeps the counts. */
export type DisplayBar = VolumeBar & Record<`${GroupKey}_v`, number>;

/**
 * What each group of each bar is drawn as. Linear: its count, stacked. Log: each group's top edge
 * at log10(1 + the logs of that group and every worse one), so red reaches the errors' height, amber
 * the warnings-or-worse height and so on, and the whole bar log10(1 + total). Either way a group with
 * any logs is raised to MIN_SEGMENT_PX of the plot's height (worked out from the tallest bar).
 */
export function displayBars(bars: VolumeBar[], scale: VolumeScale, plotHeightPx: number): DisplayBar[] {
  const order = GROUPS.map((g) => g.key);
  const raw = bars.map((b) => {
    if (scale === 'linear') return order.map((k) => b[k]);
    let below = 0;
    let cumulative = 0;
    return order.map((k) => {
      cumulative += b[k];
      const top = Math.log10(1 + cumulative);
      const height = top - below;
      below = top;
      return b[k] > 0 ? height : 0;
    });
  });
  const tallest = Math.max(0, ...raw.map((parts) => parts.reduce((a, v) => a + v, 0)));
  const floor = tallest > 0 && plotHeightPx > 0 ? (MIN_SEGMENT_PX * tallest) / plotHeightPx : 0;
  return bars.map((b, i) => {
    const shown = { ...b } as DisplayBar;
    order.forEach((k, n) => {
      shown[`${k}_v`] = b[k] > 0 ? Math.max(raw[i][n], floor) : 0;
    });
    return shown;
  });
}

/**
 * Where the pinned box goes for a bar at fraction x of the chart's width: its left edge (a bar on
 * the left half) or right edge (the right half) about half a box (6rem) before the bar, and never
 * nearer the band's sides than their 0.75rem padding.
 */
function pinnedBoxPosition(x: number): React.CSSProperties {
  const fraction = Math.min(1, Math.max(0, x));
  const bar = `(0.75rem + (100% - 1.5rem) * ${fraction})`;
  return fraction <= 0.5
    ? { left: `max(0.75rem, calc(${bar} - 6rem))` }
    : { right: `max(0.75rem, calc(100% - ${bar} - 6rem))` };
}

/** Shown while counts are on their way: a search over many days can take several seconds. */
const CountingNote: React.FC = () => (
  <span className="flex items-center gap-1.5 text-[11px] font-mono text-fg-muted">
    <Loader2 aria-hidden="true" className="w-3 h-3 motion-safe:animate-spin" />
    Counting logs...
  </span>
);

/** Only the parts of the filters the counts depend on, as a key that changes when they do. */
const filterKey = (f: LogFilterParams) =>
  JSON.stringify([f.query ?? '', f.severity_max ?? null, f.sources ?? f.source ?? [], f.apps ?? f.app_name ?? [], f.from ?? '', f.to ?? '']);

/**
 * A small bar chart of how many logs the Console's filters select per time bucket, stacked by
 * severity group, over the Console's time range (everything kept on All Time). A click anywhere
 * in a bar's column zooms the Console to it (on a touch screen, a tap pins the bar's counts in a
 * box, and a tap on the box zooms); 1-second bars, the smallest, do not zoom. While the Console follows new logs the counts are fetched again
 * every few seconds, never from the browser's own buffer, which drops logs under load.
 */
export const VolumeHistogram: React.FC<VolumeHistogramProps> = ({
  filters,
  timeZone,
  isMobile,
  onZoom,
  canZoomOut = false,
  onZoomOut,
}) => {
  const [data, setData] = useState<LogHistogram | null>(null);
  // The filters the shown counts are for: bars of earlier filters are dimmed and cannot be
  // clicked until the new counts arrive, so a click never zooms the new filters to an old bar
  const [dataKey, setDataKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The server stopped a count that took too long (504): its message, shown until the filters change
  const [tooSlow, setTooSlow] = useState<string | null>(null);
  // The last pointer on the chart: a touch pins a bar's counts instead of zooming at once
  const pointerTypeRef = useRef<string>('mouse');
  // Where a touch went down, to tell a tap from a scroll
  const touchDownRef = useRef<{ x: number; y: number } | null>(null);
  const [touchUsed, setTouchUsed] = useState(false);
  // The bar a tap pinned (its start), shown in a box that zooms when tapped
  const [pinnedStart, setPinnedStart] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  // The chart's width, which sets the bars' width (Recharts takes it from the gaps between bars,
  // which a lone bar does not have, so it drew none)
  const [chartWidth, setChartWidth] = useState(0);
  // Linear, or log so a few errors among many logs show at a readable height
  const [scale, setScale] = useState<VolumeScale>(() => readBrowserPref(SCALE_PREF, parseScale) ?? 'linear');
  const toggleScale = () => {
    const next = scale === 'log' ? 'linear' : 'log';
    setScale(next);
    writeBrowserPref(SCALE_PREF, next);
  };
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
      let failed = false;
      let bucketSeconds: number | undefined;
      try {
        const result = await fetchLogHistogram(filtersRef.current, { signal: controller.signal });
        if (cancelled) return;
        setData(result);
        setDataKey(key);
        bucketSeconds = result.bucket_seconds;
        setError(null);
        setTooSlow(null);
      } catch (err) {
        if (cancelled || (err instanceof DOMException && err.name === 'AbortError')) return;
        if (err instanceof ApiError && err.status === 504) {
          // Too slow to count: say so and stop, rather than recount every refresh
          setData(null);
          setDataKey(null);
          setError(null);
          setTooSlow(err.message);
          return;
        }
        setError(err instanceof Error ? err.message : 'request failed');
        failed = true;
      }
      // A closed range is fetched once, but tried again after a failure
      if (!cancelled && (live || failed)) schedule(failed ? HISTOGRAM_REFRESH_MS : refreshDelayMs(bucketSeconds));
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

  // Clear any pinned touch tooltip when the filters change
  useEffect(() => {
    setPinnedStart(null);
  }, [key]);

  // Dismiss the pinned touch tooltip when tapping anywhere outside the chart/box or pressing Escape
  useEffect(() => {
    if (pinnedStart === null) return;
    const onPointerDown = (e: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setPinnedStart(null);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.defaultPrevented && openDialogCount() === 0) {
        setPinnedStart(null);
      }
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [pinnedStart]);

  const bucketMs = data ? data.bucket_seconds * 1000 : 0;
  const bars = useMemo(() => (data ? data.buckets.map((b, i, all) => toBar(b, i, all, bucketMs)) : []), [data, bucketMs]);
  const shownBars = useMemo(
    () => displayBars(bars, scale, isMobile ? PLOT_HEIGHT.mobile : PLOT_HEIGHT.desktop),
    [bars, scale, isMobile],
  );
  const stale = data !== null && dataKey !== key;
  const withSeconds = data !== null && data.bucket_seconds < 60;
  // 1-second bars are the smallest: zooming into one would show a single bar
  const canZoom = data !== null && data.bucket_seconds > 1 && !stale;
  const total = bars.reduce((sum, b) => sum + b.total, 0);
  const rangeFrom = data ? Date.parse(data.range_from) : 0;
  const rangeTo = data ? Date.parse(data.range_to) : 0;
  const { ticks, stepMs } = useMemo(
    () => buildTimeTicks(rangeFrom, rangeTo, timeZone, { maxTicks: isMobile ? 4 : 8 }),
    [rangeFrom, rangeTo, timeZone, isMobile],
  );
  const spansDays = rangeTo - rangeFrom > DAY_MS;
  const domain = bars.length > 0 ? [bars[0].mid - bucketMs / 2, bars[bars.length - 1].mid + bucketMs / 2] : [0, 1];
  const plotWidth = Math.max(chartWidth - CHART_MARGIN.left - CHART_MARGIN.right, 0);
  const barSize = Math.max(1, Math.floor((plotWidth * bucketMs) / Math.max(domain[1] - domain[0], 1)) - 1);
  let caption = 'Log volume';
  if (!filters.from) caption = filters.to ? 'Log volume up to To' : 'Log volume, all logs kept';
  const pinned = pinnedStart === null ? null : (bars.find((b) => b.start === pinnedStart) ?? null);
  const zoomTo = (bar: VolumeBar) => {
    setPinnedStart(null);
    // The Console's To includes its own moment, which is the next bar's start: end a
    // millisecond before it so the list holds only this bar's logs
    onZoom(new Date(bar.start).toISOString(), new Date(bar.end - 1).toISOString());
  };
  // The column under a click, from its position: Recharts' own active index follows the hover,
  // which a tap (or a click with no move before it) has not updated yet
  const barAt = (clientX: number, box: HTMLElement): VolumeBar | null => {
    if (stale || bars.length === 0 || plotWidth <= 0) return null;
    const x = clientX - box.getBoundingClientRect().left - CHART_MARGIN.left;
    const at = domain[0] + (x / plotWidth) * (domain[1] - domain[0]);
    const bar = bars.reduce((near, b) => (Math.abs(b.mid - at) < Math.abs(near.mid - at) ? b : near));
    return Math.abs(bar.mid - at) > bucketMs / 2 ? null : bar;
  };
  // A mouse click zooms. Touches are handled on pointer up instead (onTouchTap): iOS Safari treats
  // a first tap whose hover changes the page (Recharts' column highlight) as a hover only, and
  // sends no click, so the first tap after a reload pinned nothing
  const onChartClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (pointerTypeRef.current === 'touch') return;
    const bar = barAt(e.clientX, e.currentTarget);
    if (bar && canZoom) zoomTo(bar);
  };
  const onTouchTap = (e: React.PointerEvent<HTMLDivElement>) => {
    const down = touchDownRef.current;
    touchDownRef.current = null;
    // A finger that moved was scrolling, not tapping
    if (e.pointerType !== 'touch' || !down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > TAP_SLOP_PX) return;
    const bar = barAt(e.clientX, e.currentTarget);
    if (bar) setPinnedStart((prev) => (prev === bar.start ? null : bar.start));
    else setPinnedStart(null);
  };
  let hint: string | null = null;
  if (data && !canZoom && !stale) hint = '1-second bars, the smallest';
  else if (data && !isMobile) hint = touchUsed ? 'Tap a bar for its counts' : 'Click a bar to zoom in';
  const busiest = bars.reduce<VolumeBar | null>((top, b) => (!top || b.total > top.total ? b : top), null);
  const errors = bars.reduce((sum, b) => sum + b.error, 0);
  const summary = data
    ? `${caption}: ${total.toLocaleString()} logs${errors > 0 ? ` and ${errors.toLocaleString()} ${errors === 1 ? 'error' : 'errors'}` : ''} from ${formatTimePoint(rangeFrom, timeZone, withSeconds)} to ${formatTimePoint(rangeTo, timeZone, withSeconds)}` +
      (busiest && busiest.total > 0 ? `, most between ${spanLabel(busiest, timeZone, withSeconds)} (${busiest.total.toLocaleString()})` : '') +
      (scale === 'log' ? ', on a log scale' : '')
    : `${caption}: loading`;

  return (
    // Its own layer, so a bar's tooltip shows over the log list below instead of under it
    <div ref={containerRef} className="relative z-10 shrink-0 bg-surface border-b border-line px-3 pt-1 pb-0.5 select-none" data-testid="volume-histogram">
      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-0.5 text-[11px] font-mono text-fg-muted">
        <span>
          {caption}
          {data && <span className="text-fg">: {total.toLocaleString()}</span>}
        </span>
        <span className="flex items-center gap-3 min-w-0">
        {error ? (
          <span role="status" className="text-danger-300 truncate">Could not load the counts: {error}</span>
        ) : stale ? (
          // New filters are being counted; the old bars stay dimmed below meanwhile
          <CountingNote />
        ) : (
          hint && <span>{hint}</span>
        )}
          {canZoomOut && onZoomOut && (
            <button
              type="button"
              onClick={onZoomOut}
              title="Return to the previous time range without resetting other filters"
              className="shrink-0 inline-flex items-center gap-1 rounded-xs px-1 text-accent-300 hover:text-accent-200 cursor-pointer focus-visible:outline-2 focus-visible:outline-accent-500"
            >
              <ZoomOut aria-hidden="true" className="w-3 h-3" />
              Zoom out
            </button>
          )}
          {data && (
            <button
              type="button"
              aria-pressed={scale === 'log'}
              onClick={toggleScale}
              title={scale === 'log' ? 'Show counts on a linear scale' : 'Show counts on a log scale, so a few errors among many logs show'}
              className={`shrink-0 rounded-xs px-1 cursor-pointer focus-visible:outline-2 focus-visible:outline-accent-500 ${
                scale === 'log' ? 'text-accent-300' : 'text-fg-muted hover:text-fg'
              }`}
            >
              Log scale
            </button>
          )}
        </span>
      </div>
      <div
        className={`relative ${isMobile ? 'h-14' : 'h-20'} ${stale ? 'opacity-50 pointer-events-none' : ''} ${canZoom ? 'cursor-pointer' : ''} [&_[tabindex="-1"]]:outline-none`}
        role="img"
        aria-label={summary}
        aria-busy={stale}
        // A swipe down on the chart is for its bars, not the Console's pull-to-refresh
        data-no-pull-refresh
        onPointerDown={(e) => {
          pointerTypeRef.current = e.pointerType;
          if (e.pointerType === 'touch') {
            setTouchUsed(true);
            touchDownRef.current = { x: e.clientX, y: e.clientY };
          }
        }}
        onPointerUp={onTouchTap}
        onPointerCancel={() => {
          touchDownRef.current = null;
        }}
        // Recharts gives its drawing layers tabindex -1 (none is reached with Tab), so a press would
        // focus one and the browser drew its focus box around the bars: keep focus where it was
        onMouseDown={(e) => e.preventDefault()}
        onClick={onChartClick}
      >
        {data && (
          <ResponsiveContainer width="100%" height="100%" onResize={(width) => setChartWidth(width)}>
            <BarChart
              data={shownBars}
              margin={CHART_MARGIN}
              barSize={barSize}
              // Off: its keyboard focus drew the browser's focus box around the bars on every
              // click. The chart is described by its label; the Time control zooms from the keyboard
              accessibilityLayer={false}
            >
              <XAxis
                dataKey="mid"
                type="number"
                scale="time"
                domain={domain}
                ticks={ticks}
                tickFormatter={(ms: number) => formatTimeTick(ms, stepMs, timeZone, spansDays)}
                stroke="var(--color-fg-subtle)"
                tick={{ fontSize: 10 }}
                height={AXIS_HEIGHT}
                tickLine={false}
              />
              <YAxis hide domain={[0, 'dataMax']} />
              <Tooltip
                cursor={{ fill: 'var(--color-raised)' }}
                wrapperStyle={{ zIndex: 30 }}
                // Shown in place: animated, it slid in from the chart's left edge each time it appeared
                isAnimationActive={false}
                content={({ active, payload }) => {
                  // On a touch screen the pinned box takes its place
                  const bar = active && !touchUsed ? (payload?.[0]?.payload as VolumeBar | undefined) : undefined;
                  return bar ? <BarTooltip bar={bar} timeZone={timeZone} withSeconds={withSeconds} /> : null;
                }}
              />
              {GROUPS.map((g) => (
                <Bar
                  key={g.key}
                  dataKey={`${g.key}_v`}
                  stackId="volume"
                  fill={g.color}
                  // A line in the band's colour between stacked parts, whose colours are close to each other
                  stroke="var(--color-surface)"
                  strokeWidth={1}
                  isAnimationActive={false}
                >
                  {/* The tapped bar is drawn darker, which shows even when it fills the chart's height */}
                  {bars.map((b) => (
                    <Cell key={b.start} fillOpacity={pinned && !stale && b.start === pinned.start ? PINNED_OPACITY : 1} />
                  ))}
                </Bar>
              ))}
            </BarChart>
          </ResponsiveContainer>
        )}
        {!data && !tooSlow && !error && (
          <div className="absolute inset-0 flex items-center justify-center">
            <CountingNote />
          </div>
        )}
        {tooSlow && !data && (
          <div role="status" className="absolute inset-0 flex items-center justify-center px-2 text-center text-[11px] font-mono text-fg-muted">
            {tooSlow}
          </div>
        )}
        {data && total === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-[11px] font-mono text-fg-muted pointer-events-none">
            No logs in this range
          </div>
        )}
      </div>
      {pinned && !stale && (
        // Below the chart (over the top of the log list), under its bar, so the bars stay in view and
        // another can be tapped. Anchored by the edge nearer its bar, never past the band's padding:
        // centred with a shift of half its width, a box for a bar near the left ran off the screen
        <div
          className="absolute top-full z-20 mt-1 max-w-[calc(100%-1.5rem)]"
          style={pinnedBoxPosition((pinned.mid - domain[0]) / (domain[1] - domain[0]))}
        >
          {canZoom ? (
            <button type="button" className="block cursor-pointer" onClick={() => zoomTo(pinned)}>
              <BarTooltip bar={pinned} timeZone={timeZone} withSeconds={withSeconds} zoomHint="Tap here to zoom in" />
            </button>
          ) : (
            <BarTooltip bar={pinned} timeZone={timeZone} withSeconds={withSeconds} />
          )}
        </div>
      )}
    </div>
  );
};
