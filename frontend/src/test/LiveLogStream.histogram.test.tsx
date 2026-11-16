// The volume histogram in the Console: its toolbar toggle, remembered by the browser, and a
// click on a bar zooming the Console to it, undone by the browser's Back button.
import type React from 'react';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LiveLogStream } from '../components/logs/LiveLogStream.tsx';
import * as logsApi from '../api/logs.ts';
import { renderWithContext, setupConsoleMocks } from './liveLogStreamSetup.tsx';

vi.mock('@tanstack/react-virtual', async () => (await import('./liveLogStreamVirtualizer.ts')).virtualizerMock);

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

const PREF_KEY = 'logshed.console.histogram.v1';

describe('Console volume histogram', () => {
  setupConsoleMocks();

  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState(null, '', '/');
    vi.spyOn(logsApi, 'fetchLogHistogram').mockResolvedValue({
      range_from: '2026-03-28T11:00:00+00:00',
      range_to: '2026-03-28T12:00:00+00:00',
      bucket_seconds: 1800,
      buckets: [
        { start: '2026-03-28T11:00:00+00:00', end: '2026-03-28T11:30:00+00:00', error: 3, warning: 0, info: 0, debug: 0 },
        { start: '2026-03-28T11:30:00+00:00', end: '2026-03-28T12:00:00+00:00', error: 1, warning: 0, info: 0, debug: 0 },
      ],
    });
  });

  it('is hidden at first, and the toolbar button shows it and is remembered', async () => {
    const first = renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);
    const toggle = await screen.findByRole('button', { name: 'Show the log volume chart' });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByTestId('volume-histogram')).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByTestId('volume-histogram')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Hide the log volume chart' })).toHaveAttribute('aria-pressed', 'true');
    expect(window.localStorage.getItem(PREF_KEY)).toBe('true');

    first.unmount();
    renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);
    expect(await screen.findByTestId('volume-histogram')).toBeInTheDocument();
  });

  it('zooms the Console to a clicked bar, and Back returns to the range before', async () => {
    window.localStorage.setItem(PREF_KEY, 'true');
    window.history.replaceState(null, '', '/?time=1h');
    const { container } = renderWithContext(<LiveLogStream onDiagnoseAi={vi.fn()} />);

    await waitFor(() => expect(container.querySelector('.recharts-bar-rectangle')).not.toBeNull());
    const fetchLogs = vi.mocked(logsApi.fetchLogs);
    fetchLogs.mockClear();
    fireEvent.click(container.querySelector('.recharts-bar-rectangle')!);

    await waitFor(() =>
      expect(fetchLogs).toHaveBeenCalledWith(
        expect.objectContaining({ from: '2026-03-28T11:00:00.000Z', to: '2026-03-28T11:29:59.999Z' }),
        expect.anything(),
      ),
    );
    const zoomed = new URLSearchParams(window.location.search);
    expect(zoomed.get('time')).toBe('2026-03-28T11:00:00.000Z');
    expect(zoomed.get('to')).toBe('2026-03-28T11:29:59.999Z');

    // A second click on the bar now shown adds no history entry, so one Back still returns
    const entries = window.history.length;
    fireEvent.click(container.querySelector('.recharts-bar-rectangle')!);
    expect(window.history.length).toBe(entries);

    fetchLogs.mockClear();
    await act(async () => {
      window.history.back();
      await new Promise((resolve) => window.addEventListener('popstate', resolve, { once: true }));
    });
    await waitFor(() => expect(fetchLogs).toHaveBeenCalled());
    const lastCall = fetchLogs.mock.calls[fetchLogs.mock.calls.length - 1][0];
    expect(lastCall?.from).toBe('1h');
    expect(lastCall?.to).toBeUndefined();
    expect(window.location.search).toBe('?time=1h');
  });
});
