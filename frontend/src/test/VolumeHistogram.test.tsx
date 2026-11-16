import type React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as logsApi from '../api/logs.ts';
import type { LogHistogram } from '../api/logs.ts';
import { HISTOGRAM_DEBOUNCE_MS, HISTOGRAM_REFRESH_MS, refreshDelayMs, VolumeHistogram } from '../components/logs/VolumeHistogram.tsx';
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

  it('shows the total and describes the chart for screen readers', async () => {
    render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(screen.getByText('Log volume, all logs kept')).toBeInTheDocument();
    expect(screen.getByText(': 8')).toBeInTheDocument();
    expect(chartBox()).toHaveAccessibleName(
      'Log volume, all logs kept: 8 logs from 3/28 11:00 to 3/28 12:00, most between 3/28 11:00 - 11:30 (7)',
    );
  });

  it('shows the time on the server clock', async () => {
    render(<VolumeHistogram filters={{}} timeZone="Asia/Tokyo" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(chartBox().getAttribute('aria-label')).toContain('from 3/28 20:00 to 3/28 21:00');
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

  it('on a touch screen pins a tapped bar\'s counts in a box that zooms when tapped', async () => {
    const onZoom = vi.fn();
    render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile onZoom={onZoom} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    fireEvent.pointerDown(chartBox(), { pointerType: 'touch' });
    clickChartAt(200);
    expect(onZoom).not.toHaveBeenCalled();
    const box = screen.getByRole('button', { name: /Tap here to zoom in/ });
    expect(box).toHaveTextContent('3/28 11:00 - 11:30: 7');
    fireEvent.click(box);
    expect(onZoom).toHaveBeenCalledWith('2026-03-28T11:00:00.000Z', '2026-03-28T11:29:59.999Z');
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
    expect(chartBox().getAttribute('aria-label')).toContain('most between 3/28 11:00:00 - 11:00:15 (3)');
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
