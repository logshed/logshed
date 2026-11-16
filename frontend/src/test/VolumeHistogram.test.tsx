import type React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as logsApi from '../api/logs.ts';
import type { LogHistogram } from '../api/logs.ts';
import { HISTOGRAM_DEBOUNCE_MS, HISTOGRAM_REFRESH_MS, VolumeHistogram } from '../components/logs/VolumeHistogram.tsx';
import { demoHistogram } from '../demo/server.ts';

// ResponsiveContainer measures nothing in jsdom: give the chart a fixed size so its bars render
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>();
  const { cloneElement } = await import('react');
  const mocked = {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactElement<any> }) =>
      cloneElement(children, { width: 800, height: 80 }),
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
    expect(screen.getByText('Log volume, last hour')).toBeInTheDocument();
    expect(screen.getByText(': 8')).toBeInTheDocument();
    expect(screen.getByRole('img')).toHaveAccessibleName(
      'Log volume, last hour: 8 logs from 3/28 11:00 to 3/28 12:00, most between 3/28 11:00 - 11:30 (7)',
    );
  });

  it('shows the time on the server clock', async () => {
    render(<VolumeHistogram filters={{}} timeZone="Asia/Tokyo" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('from 3/28 20:00 to 3/28 21:00');
  });

  it('says when the range has no logs', async () => {
    fetchSpy.mockResolvedValue({ ...HOUR, buckets: HOUR.buckets.map((b) => bucket(b.start, b.end)) });
    render(<VolumeHistogram filters={{ from: '1h' }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    expect(screen.getByText('No logs in this range')).toBeInTheDocument();
    expect(screen.getByText('Log volume')).toBeInTheDocument();
  });

  it('zooms to a bar when it is clicked', async () => {
    const onZoom = vi.fn();
    const { container } = render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={onZoom} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    // Bars draw in order of the stack, error first: the first is the 11:00 bucket's errors
    const rect = container.querySelector('.recharts-bar-rectangle path, .recharts-bar-rectangle');
    expect(rect).not.toBeNull();
    fireEvent.click(rect!);
    expect(onZoom).toHaveBeenCalledWith('2026-03-28T11:00:00.000Z', '2026-03-28T11:30:00.000Z');
  });

  it('fetches again every few seconds while the Console follows new logs', async () => {
    render(<VolumeHistogram filters={{ from: '1h' }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_REFRESH_MS));
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_REFRESH_MS));
    expect(fetchSpy).toHaveBeenCalledTimes(3);
  });

  it('fetches a closed range once', async () => {
    render(
      <VolumeHistogram filters={{ from: HOUR.range_from, to: HOUR.range_to }} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />,
    );
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS + 3 * HISTOGRAM_REFRESH_MS));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it('stops fetching while the tab is hidden and catches up when it is shown', async () => {
    render(<VolumeHistogram filters={{}} timeZone="UTC" isMobile={false} onZoom={vi.fn()} />);
    await act(() => vi.advanceTimersByTimeAsync(HISTOGRAM_DEBOUNCE_MS));
    setVisibility('hidden');
    await act(() => vi.advanceTimersByTimeAsync(5 * HISTOGRAM_REFRESH_MS));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    setVisibility('visible');
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
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
