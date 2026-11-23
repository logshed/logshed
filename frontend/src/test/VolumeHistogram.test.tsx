import type React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as logsApi from '../api/logs.ts';
import { ApiError } from '../api/client.ts';
import type { LogHistogram } from '../api/logs.ts';
import {
  HISTOGRAM_DEBOUNCE_MS,
  HISTOGRAM_REFRESH_MS,
  MIN_SEGMENT_PX,
  SCALE_PREF,
  displayBars,
  refreshDelayMs,
  DisplayBar,
  VolumeBar,
  VolumeHistogram,
  spanLabel,
} from '../components/logs/VolumeHistogram.tsx';
import { demoHistogram } from '../demo/server.ts';

// ResponsiveContainer measures nothing in jsdom: give the chart a fixed size so its bars render
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>();
  const { cloneElement, useEffect } = await import('react');
  const mocked = {
    ...actual,
    ResponsiveContainer: ({ children, onResize }: { children: React.ReactElement<any>; onResize?: (w: number, h: number) => void }) => {
      // Report the size, as the real one does once it has measured the box
      useEffect(() => onResize?.(804, 80), [onResize]);
      return cloneElement(children, { width: 804, height: 80 });
    },
  };
  return { ...mocked, default: mocked };
});

const bucket = (start: string, end: string, counts: Partial<Record<'error' | 'warning' | 'info' | 'debug', number>> = {}) => ({
  start, end, error: 0, warning: 0, info: 0, debug: 0, ...counts,
});

const HOUR: LogHistogram = {
  range_from: '2026-03-28T11:00:00+00:00',
  range_to: '2026-03-28T12:00:00+00:00',
  bucket_seconds: 1800,
  buckets: [
    bucket('2026-03-28T11:00:00+00:00', '2026-03-28T11:30:00+00:00', { error: 2, info: 5 }),
    bucket('2026-03-28T11:30:00+00:00', '2026-03-28T12:00:00+00:00', { warning: 1 }),
  ],
};

let fetchSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  vi.useFakeTimers();
  fetchSpy = vi.spyOn(logsApi, 'fetchLogHistogram').mockResolvedValue(HOUR);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
});

/** The chart's box (its role=img div), clicked x px from its left edge (jsdom puts it at 0). */
const chartBox = () => screen.getAllByRole('img').find((el) => el.tagName === 'DIV')!;
const clickChartAt = (x: number) => fireEvent.click(chartBox(), { clientX: x });

const setVisibility = (state: 'visible' | 'hidden') => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: state });
  document.dispatchEvent(new Event('visibilitychange'));
};

describe('VolumeHistogram', () => {
  it('fetches the counts for the Console filters once typing stops', async () => {
    const onZoom = vi.fn();
    const { rerender } = render(<VolumeHistogram filters={{ query: 'dis' }} timeZone="UTC" isMobile={false} onZoom={onZoom} />);
    rerender(<VolumeHistogram filters={{ query: 'disk', apps: ['kernel'] }} timeZone="UTC" isMobile={false} onZoom={onZoom} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toEqual({ query: 'disk', apps: ['kernel'] });
  });

  it('says it is counting until the first counts arrive, and while new filters are counted', async () => {
    let answer: (value: LogHistogram) => void = () => {};
    fetchSpy.mockImplementation(() => new Promise<LogHistogram>((resolve) => { answer = resolve; }));
    const { rerender } = render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    expect(screen.getByText('Counting logs...')).toBeInTheDocument();
    // No bars yet, so nothing to click
    expect(screen.queryByText('Click a bar to zoom in')).not.toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    await act(async () => answer(HOUR));
    expect(screen.queryByText('Counting logs...')).not.toBeInTheDocument();
    expect(screen.getByText('Click a bar to zoom in')).toBeInTheDocument();
    rerender(<VolumeHistogram filters={{ query: 'disk' }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    expect(screen.getByText('Counting logs...')).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    await act(async () => answer(HOUR));
    expect(screen.queryByText('Counting logs...')).not.toBeInTheDocument();
  });

  it('shows the total and describes the chart for screen readers', async () => {
    render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(screen.getByText('Log volume, all logs kept')).toBeInTheDocument();
    expect(screen.getByText(': 8')).toBeInTheDocument();
    expect(chartBox()).toHaveAccessibleName(
      'Log volume, all logs kept: 8 logs and 2 errors from 28 Mar 11:00 to 28 Mar 12:00, most between 28 Mar 11:00 - 11:30 (7)',
    );
  });

  it('shows the time on the server clock', async () => {
    render(<VolumeHistogram filters={{}} timeZone="Asia/Tokyo" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(chartBox().getAttribute('aria-label')).toContain('from 28 Mar 20:00 to 28 Mar 21:00');
  });

  it('says when the range has no logs', async () => {
    fetchSpy.mockResolvedValue({ ...HOUR, buckets: HOUR.buckets.map((b) => bucket(b.start, b.end)) });
    render(<VolumeHistogram filters={{ from: '1h' }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(screen.getByText('No logs in this range')).toBeInTheDocument();
    expect(screen.getByText('Log volume')).toBeInTheDocument();
  });

  it('zooms to the bar whose column is clicked, however short the bar', async () => {
    const onZoom = vi.fn();
    render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={onZoom} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    // The plot is 800 px for 11:00 to 12:00: 200 px is 11:15, in the first bar's column
    clickChartAt(200);
    // The Console's To includes its own moment: the zoom ends a millisecond before the next bar
    expect(onZoom).toHaveBeenLastCalledWith('2026-03-28T11:00:00.000Z', '2026-03-28T11:29:59.999Z');
    clickChartAt(700);
    expect(onZoom).toHaveBeenLastCalledWith('2026-03-28T11:30:00.000Z', '2026-03-28T11:59:59.999Z');
  });

  it('shows the Zoom out button only when canZoomOut is true and calls onZoomOut', async () => {
    const onZoomOut = vi.fn();
    const { rerender } = render(
      <VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={vi.fn()} canZoomOut={false} onZoomOut={onZoomOut} />,
    );
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(screen.queryByRole('button', { name: 'Zoom out' })).not.toBeInTheDocument();

    rerender(
      <VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={vi.fn()} canZoomOut onZoomOut={onZoomOut} />,
    );
    const zoomOutBtn = screen.getByRole('button', { name: 'Zoom out' });
    fireEvent.click(zoomOutBtn);
    expect(onZoomOut).toHaveBeenCalledTimes(1);
  });

  it('on a touch screen pins a tapped bar\'s counts in a box that zooms when tapped', async () => {
    const onZoom = vi.fn();
    render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile onZoom={onZoom} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    // What iOS Safari sends for a first tap after a reload: pointer down and up, and no click
    fireEvent.pointerDown(chartBox(), { pointerType: 'touch', clientX: 200, clientY: 30 });
    fireEvent.pointerUp(chartBox(), { pointerType: 'touch', clientX: 202, clientY: 31 });
    expect(onZoom).not.toHaveBeenCalled();
    // The tapped bar (the first) is drawn darker than the others
    const opacities = [...document.querySelectorAll('.recharts-bar-rectangle path')].map((el) => el.getAttribute('fill-opacity'));
    expect(opacities).toContain('0.45');
    expect(opacities).toContain('1');
    const box = screen.getByRole('button', { name: /Tap here to zoom in/ });
    expect(box).toHaveTextContent('28 Mar 11:00 - 11:30: 7');
    fireEvent.click(box);
    expect(onZoom).toHaveBeenCalledWith('2026-03-28T11:00:00.000Z', '2026-03-28T11:29:59.999Z');
    expect(screen.queryByRole('button', { name: /Tap here to zoom in/ })).not.toBeInTheDocument();
  });

  it('closes the pinned touch tooltip when tapping outside, tapping the same bar again, or pressing Escape', async () => {
    render(
      <div>
        <VolumeHistogram filters={{}} timeZone="UTC" isMobile onZoom={vi.fn()} />
        <button type="button">Log row outside</button>
      </div>,
    );
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));

    // Pin the first bar
    fireEvent.pointerDown(chartBox(), { pointerType: 'touch', clientX: 200, clientY: 30 });
    fireEvent.pointerUp(chartBox(), { pointerType: 'touch', clientX: 200, clientY: 30 });
    expect(screen.getByRole('button', { name: /Tap here to zoom in/ })).toBeInTheDocument();

    // Tapping outside (e.g. a log row) dismisses the pinned box
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Log row outside' }), { pointerType: 'touch' });
    expect(screen.queryByRole('button', { name: /Tap here to zoom in/ })).not.toBeInTheDocument();

    // Pin again, then tap the same bar to toggle it off
    fireEvent.pointerDown(chartBox(), { pointerType: 'touch', clientX: 200, clientY: 30 });
    fireEvent.pointerUp(chartBox(), { pointerType: 'touch', clientX: 200, clientY: 30 });
    expect(screen.getByRole('button', { name: /Tap here to zoom in/ })).toBeInTheDocument();
    fireEvent.pointerDown(chartBox(), { pointerType: 'touch', clientX: 200, clientY: 30 });
    fireEvent.pointerUp(chartBox(), { pointerType: 'touch', clientX: 200, clientY: 30 });
    expect(screen.queryByRole('button', { name: /Tap here to zoom in/ })).not.toBeInTheDocument();

    // Pin again, then press Escape to dismiss
    fireEvent.pointerDown(chartBox(), { pointerType: 'touch', clientX: 200, clientY: 30 });
    fireEvent.pointerUp(chartBox(), { pointerType: 'touch', clientX: 200, clientY: 30 });
    expect(screen.getByRole('button', { name: /Tap here to zoom in/ })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('button', { name: /Tap here to zoom in/ })).not.toBeInTheDocument();
  });

  it('does not take a finger that moves over the chart for a tap', async () => {
    render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    fireEvent.pointerDown(chartBox(), { pointerType: 'touch', clientX: 200, clientY: 30 });
    fireEvent.pointerUp(chartBox(), { pointerType: 'touch', clientX: 260, clientY: 30 });
    expect(screen.queryByRole('button', { name: /Tap here to zoom in/ })).not.toBeInTheDocument();
  });

  it('does not zoom into 1-second bars, the smallest, and still draws a lone bar', async () => {
    fetchSpy.mockResolvedValue({
      range_from: '2026-03-28T11:00:02+00:00',
      range_to: '2026-03-28T11:00:03+00:00',
      bucket_seconds: 1,
      buckets: [bucket('2026-03-28T11:00:02+00:00', '2026-03-28T11:00:03+00:00', { info: 8 })],
    });
    const onZoom = vi.fn();
    const { container } = render(
      <VolumeHistogram filters={{ from: '2026-03-28T11:00:02Z', to: '2026-03-28T11:00:02.999Z' }} timeZone="UTC" isMobile={false} onZoom={onZoom} />,
    );
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(screen.getByText('1-second bars, the smallest')).toBeInTheDocument();
    const rects = container.querySelectorAll('.recharts-bar-rectangle');
    expect(rects).toHaveLength(1);
    clickChartAt(400);
    expect(onZoom).not.toHaveBeenCalled();
  });

  it('dims the old bars and ignores clicks while new filters load', async () => {
    const onZoom = vi.fn();
    const { rerender } = render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={onZoom} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    rerender(<VolumeHistogram filters={{ query: 'disk' }} timeZone="UTC" isMobile={false} onZoom={onZoom} />);
    expect(chartBox()).toHaveAttribute('aria-busy', 'true');
    clickChartAt(200);
    expect(onZoom).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(chartBox()).toHaveAttribute('aria-busy', 'false');
  });

  it('shows seconds for bars under a minute', async () => {
    fetchSpy.mockResolvedValue({
      range_from: '2026-03-28T11:00:00+00:00',
      range_to: '2026-03-28T11:00:30+00:00',
      bucket_seconds: 15,
      buckets: [
        bucket('2026-03-28T11:00:00+00:00', '2026-03-28T11:00:15+00:00', { info: 3 }),
        bucket('2026-03-28T11:00:15+00:00', '2026-03-28T11:00:30+00:00', { info: 1 }),
      ],
    });
    render(<VolumeHistogram filters={{ from: '15m' }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(chartBox().getAttribute('aria-label')).toContain('most between 28 Mar 11:00:00 - 11:00:15 (3)');
  });

  it('says up to To when only To is set', async () => {
    render(<VolumeHistogram filters={{ to: HOUR.range_to }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(screen.getByText('Log volume up to To')).toBeInTheDocument();
  });

  it('fetches again every few seconds while the Console follows new logs', async () => {
    // Minute bars: a sixth of a bar is under the 10 s floor
    fetchSpy.mockResolvedValue({ ...HOUR, bucket_seconds: 60 });
    render(<VolumeHistogram filters={{ from: '1h' }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_REFRESH_MS));
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_REFRESH_MS));
    expect(fetchSpy).toHaveBeenCalledTimes(3);
  });

  it('refreshes less often for wide bars', async () => {
    // HOUR has 30-minute bars: a sixth of a bar is over the 60 s ceiling
    expect(refreshDelayMs(15)).toBe(10_000);
    expect(refreshDelayMs(300)).toBe(50_000);
    expect(refreshDelayMs(3600)).toBe(60_000);
    render(<VolumeHistogram filters={{ from: '1h' }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    await act(() => vi.advanceTimersByTimeAsync(59_000));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it('fetches a closed range once', async () => {
    render(
      <VolumeHistogram filters={{ from: HOUR.range_from, to: HOUR.range_to }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />,
    );
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS + 3 * HISTOGRAM_REFRESH_MS));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('stops fetching while the tab is hidden and catches up when it is shown', async () => {
    fetchSpy.mockResolvedValue({ ...HOUR, bucket_seconds: 60 });
    render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    setVisibility('hidden');
    await act(() => vi.advanceTimersByTimeAsync(5 * HISTOGRAM_REFRESH_MS));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    setVisibility('visible');
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it('says when a count takes too long and stops fetching until the filters change', async () => {
    fetchSpy.mockRejectedValueOnce(new ApiError(504, 'Too many logs to count quickly: pick a shorter time range.'));
    const { rerender } = render(<VolumeHistogram filters={{ query: 'error' }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(screen.getByRole('status')).toHaveTextContent('Too many logs to count quickly: pick a shorter time range.');
    await act(() => vi.advanceTimersByTimeAsync(5 * HISTOGRAM_REFRESH_MS));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    rerender(<VolumeHistogram filters={{ query: 'error', from: '24h' }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/Too many logs/)).not.toBeInTheDocument();
  });

  it('tries a closed range again after a failure', async () => {
    fetchSpy.mockRejectedValueOnce(new Error('Database is busy'));
    render(
      <VolumeHistogram filters={{ from: HOUR.range_from, to: HOUR.range_to }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />,
    );
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS + 3 * HISTOGRAM_REFRESH_MS));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('shows a failed fetch and tries again at the next refresh', async () => {
    fetchSpy.mockRejectedValueOnce(new Error('Database is busy'));
    render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(screen.getByRole('status')).toHaveTextContent('Could not load the counts: Database is busy');
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_REFRESH_MS));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});

const scaleBar = (start: number, counts: Partial<Record<'error' | 'warning' | 'info' | 'debug', number>>): VolumeBar => {
  const b = { error: 0, warning: 0, info: 0, debug: 0, ...counts };
  return { start, end: start + 1, mid: start, total: b.error + b.warning + b.info + b.debug, ...b };
};
const drawnTotal = (b: DisplayBar) => b.error_v + b.warning_v + b.info_v + b.debug_v;

describe('a bar\'s span label', () => {
  const bar = (start: string, end: string) =>
    ({ start: Date.parse(start), end: Date.parse(end), mid: 0, total: 0, error: 0, warning: 0, info: 0, debug: 0 });

  it('names the date once when the bar starts and ends on the same day, and both dates across midnight', () => {
    expect(spanLabel(bar('2026-03-28T11:00:00Z', '2026-03-28T11:30:00Z'), 'UTC')).toBe('28 Mar 11:00 - 11:30');
    expect(spanLabel(bar('2026-03-28T23:00:00Z', '2026-03-29T00:00:00Z'), 'UTC')).toBe('28 Mar 23:00 - 29 Mar 00:00');
    expect(spanLabel(bar('2026-03-28T11:00:00Z', '2026-03-28T11:00:15Z'), 'UTC', true)).toBe('28 Mar 11:00:00 - 11:00:15');
  });
});

describe('the chart scales', () => {
  it('draws counts on the linear scale, with a severity that has logs at least 3 px tall', () => {
    const [busy, quiet] = displayBars([scaleBar(0, { info: 50_000 }), scaleBar(1, { error: 1, info: 25_000 })], 'linear', 60);
    expect([busy.info_v, busy.error_v]).toEqual([50_000, 0]);
    // One error among 25,001 logs: 0.001 px by its count, drawn MIN_SEGMENT_PX tall
    expect((quiet.error_v / drawnTotal(busy)) * 60).toBeCloseTo(MIN_SEGMENT_PX, 5);
    expect(quiet.info_v).toBe(25_000);
    // The counts themselves are kept for the tooltip
    expect([quiet.error, quiet.info, quiet.total]).toEqual([1, 25_000, 25_001]);
  });

  it('puts each colour on the log scale at the logs of its severity and every worse one', () => {
    // The maintainer's day on 18 Nov: 89 errors, 133 warnings, 53,064 info
    const [bar] = displayBars([scaleBar(0, { error: 89, warning: 133, info: 53_064 })], 'log', 60);
    expect(bar.error_v).toBeCloseTo(Math.log10(90), 6);
    expect(bar.error_v + bar.warning_v).toBeCloseTo(Math.log10(1 + 222), 6);
    expect(drawnTotal(bar)).toBeCloseTo(Math.log10(1 + 53_286), 6);
    expect(bar.debug_v).toBe(0);
    // Red reaches about 40% of the bar instead of a fraction of a pixel
    expect(bar.error_v / drawnTotal(bar)).toBeGreaterThan(0.4);
  });

  it('keeps the floor on the log scale and draws nothing for an empty group or range', () => {
    const [big, small] = displayBars([scaleBar(0, { info: 1_000_000 }), scaleBar(1, { error: 1 })], 'log', 36);
    expect((small.error_v / drawnTotal(big)) * 36).toBeGreaterThanOrEqual(MIN_SEGMENT_PX - 1e-9);
    expect([small.warning_v, small.info_v, small.debug_v]).toEqual([0, 0, 0]);
    expect(displayBars([], 'log', 60)).toEqual([]);
    expect(displayBars([scaleBar(0, {})], 'linear', 60)[0].info_v).toBe(0);
  });

  it('switches to a log scale and remembers it in this browser', async () => {
    localStorage.removeItem(`logshed.${SCALE_PREF}`);
    const { unmount } = render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    const toggle = screen.getByRole('button', { name: 'Log scale' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(chartBox().getAttribute('aria-label')).not.toContain('log scale');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    expect(chartBox().getAttribute('aria-label')).toMatch(/, on a log scale$/);
    expect(localStorage.getItem(`logshed.${SCALE_PREF}`)).toBe('"log"');
    unmount();

    render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(screen.getByRole('button', { name: 'Log scale' })).toHaveAttribute('aria-pressed', 'true');
    localStorage.removeItem(`logshed.${SCALE_PREF}`);
  });
});

describe('the demo histogram', () => {
  it('buckets the demo logs over the last hour by severity group', () => {
    const result = demoHistogram(new URLSearchParams(), Date.parse('2026-03-28T12:00:00Z'));
    expect(result.range_from).toBe('2026-03-28T11:00:00.000Z');
    expect(result.bucket_seconds).toBe(60);
    expect(result.buckets).toHaveLength(60);
  });

  it('reads relative and absolute ranges', () => {
    const now = Date.parse('2026-03-28T12:00:00Z');
    expect(demoHistogram(new URLSearchParams({ from: '24h' }), now).bucket_seconds).toBe(3600);
    const closed = demoHistogram(new URLSearchParams({ from: '2026-03-28T10:00:00Z', to: '2026-03-28T10:15:00Z' }), now);
    expect(closed.bucket_seconds).toBe(15);
    expect(closed.range_to).toBe('2026-03-28T10:15:00.000Z');
  });
});
