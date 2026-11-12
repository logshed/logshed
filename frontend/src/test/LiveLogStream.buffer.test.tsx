// The buffer cap, live rows while scrolled back or scrolling, and row keys.
import { screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { MOBILE_LOG_ROW_HEIGHT, DESKTOP_LOG_ROW_HEIGHT } from '../components/logs/LogRow.tsx';
import * as logsApi from '../api/logs.ts';
import { LogEntry } from '../types.ts';
import { MockEventSource, virtualizerControl, sampleLogs, setupStreamMocks } from './liveLogStreamSetup.tsx';

vi.mock('@tanstack/react-virtual', async () => (await import('./liveLogStreamVirtualizer.ts')).virtualizerMock);

describe('Console buffer and live rows', () => {
  const { renderStream } = setupStreamMocks();

  describe('buffer cap', () => {
    const makeLog = (id: number, label: string): LogEntry => ({
      id,
      timestamp: new Date(Date.UTC(2026, 8, 3, 12, 0, 0) + id).toISOString(),
      received_at: new Date(Date.UTC(2026, 8, 3, 12, 0, 0) + id).toISOString(),
      source_ip: '192.168.1.50',
      source_alias: 'homelab-host',
      app_name: 'nginx',
      facility: 1,
      severity: 6,
      message: `${label} ${id}`,
      raw: 'raw',
    });

    // History pages: offset N returns the rows ranked N..N+499 in a 100,000-row result set (newest first)
    const mockHistory = () =>
      vi.spyOn(logsApi, 'fetchLogs').mockImplementation(async (params: any) => {
        const offset = params.offset ?? 0;
        return {
          logs: Array.from({ length: 500 }, (_, i) => makeLog(100000 - offset - i, 'History')),
          total: 100000,
          limit: 500,
          offset,
        };
      });

    const setupScroller = () => {
      const scroller = document.querySelector('.overflow-y-auto') as HTMLElement;
      let top = 0;
      Object.defineProperty(scroller, 'scrollTop', { get: () => top, set: (v: number) => { top = v; }, configurable: true });
      Object.defineProperty(scroller, 'scrollHeight', { get: () => 10000, configurable: true });
      Object.defineProperty(scroller, 'clientHeight', { get: () => 500, configurable: true });
      return {
        // Near the bottom: pauses auto-scroll and loads the next page
        toBottom: () => { top = 9400; fireEvent.scroll(scroller); },
        toTop: () => { top = 0; fireEvent.scroll(scroller); },
      };
    };

    const bufferCount = () =>
      Number(screen.getByText(/Screen Buffer:/).textContent!.replace(/[^0-9]/g, ''));

    const loadPages = async (fetchSpy: ReturnType<typeof mockHistory>, scroller: ReturnType<typeof setupScroller>, pages: number) => {
      for (let p = 1; p < pages; p++) {
        scroller.toBottom();
        await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith(expect.objectContaining({ offset: p * 500 }), expect.anything()));
        await waitFor(() => expect(bufferCount()).toBe((p + 1) * 500));
      }
    };

    const emitLive = (count: number) => {
      const es = MockEventSource.instances[MockEventSource.instances.length - 1];
      act(() => {
        for (let i = 0; i < count; i++) es.emit('log', makeLog(200000 + i, 'Live'));
      });
    };

    it('keeps the newest 3,000 rows while auto-scroll is on and pages on without a gap', async () => {
      virtualizerControl.edgeRows = 20;
      virtualizerControl.headOnly = true;
      const fetchSpy = mockHistory();
      renderStream();
      await waitFor(() => expect(bufferCount()).toBe(500));
      const scroller = setupScroller();
      await loadPages(fetchSpy, scroller, 5);

      scroller.toTop();
      expect(screen.getByText('Auto-Scroll ON')).toBeInTheDocument();
      emitLive(600);
      // The last 100 rows flush on the 100 ms batch timer; the newest row shows at the top
      await waitFor(() => expect(screen.getByText('Live 200599')).toBeInTheDocument());
      await waitFor(() => expect(bufferCount()).toBe(3000));

      // 100 of the 2,500 history rows were trimmed, so the next page starts at offset 2,400
      fetchSpy.mockClear();
      scroller.toBottom();
      await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
      expect(fetchSpy).toHaveBeenCalledWith(expect.objectContaining({ offset: 2400 }), expect.anything());
    });

    it('does not trim scrolled-back history when live rows arrive while paused', async () => {
      virtualizerControl.edgeRows = 20;
      virtualizerControl.headOnly = true;
      const fetchSpy = mockHistory();
      renderStream();
      await waitFor(() => expect(bufferCount()).toBe(500));
      const scroller = setupScroller();
      await loadPages(fetchSpy, scroller, 5);
      expect(screen.getByText('Paused')).toBeInTheDocument();

      emitLive(600);
      await waitFor(() => expect(bufferCount()).toBe(3100));
      await waitFor(() => expect(screen.getByText(/600 new logs at top/)).toBeInTheDocument());
    });

    it('holds live rows back while scrolled back with a full buffer and adds them on resume', async () => {
      virtualizerControl.edgeRows = 20;
      virtualizerControl.headOnly = true;
      // Large pages so the 20,000-row scrollback buffer fills in a few loads
      const fetchSpy = vi.spyOn(logsApi, 'fetchLogs').mockImplementation(async (params: any) => {
        const offset = params.offset ?? 0;
        return {
          logs: Array.from({ length: 5000 }, (_, i) => makeLog(100000 - offset - i, 'History')),
          total: 100000,
          limit: 500,
          offset,
        };
      });
      renderStream();
      // Auto-scroll is on at first, so the first page is cut to the newest 3,000 rows
      await waitFor(() => expect(bufferCount()).toBe(3000));
      const scroller = setupScroller();
      for (const expected of [8000, 13000, 18000, 20000]) {
        scroller.toBottom();
        await waitFor(() => expect(bufferCount()).toBe(expected));
      }
      expect(screen.getByText(/Screen buffer limit reached/)).toBeInTheDocument();
      fetchSpy.mockClear();
      scroller.toBottom();
      expect(fetchSpy).not.toHaveBeenCalled();

      emitLive(10);
      await waitFor(() => expect(screen.getByText(/10 new logs at top/)).toBeInTheDocument());
      expect(bufferCount()).toBe(20000);
      expect(screen.queryByText('Live 200009')).toBeNull();

      scroller.toTop();
      await waitFor(() => expect(screen.getByText('Live 200009')).toBeInTheDocument());
      expect(bufferCount()).toBe(3000);
    });
  });

  it('anchors the scroll position with the mobile row height after crossing the breakpoint', async () => {
    const listeners: ((e: { matches: boolean }) => void)[] = [];
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: (_: string, cb: (e: { matches: boolean }) => void) => listeners.push(cb),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as any;
    renderStream();
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());

    // Switch to the mobile layout
    act(() => listeners.forEach((cb) => cb({ matches: true })));
    await waitFor(() => expect(screen.queryByText('TIMESTAMP')).toBeNull());

    const scroller = document.querySelector('.overflow-y-auto') as HTMLElement;
    let top = 200;
    Object.defineProperty(scroller, 'scrollTop', { get: () => top, set: (v: number) => { top = v; }, configurable: true });
    fireEvent.scroll(scroller);
    expect(screen.getByText('Paused')).toBeInTheDocument();

    const es = MockEventSource.instances[MockEventSource.instances.length - 1];
    act(() => {
      for (let i = 0; i < 3; i++) {
        es.emit('log', { ...sampleLogs[0], id: 900 + i, message: `Mobile anchored ${i}` });
      }
    });
    await waitFor(() => expect(screen.getByText(/3 new logs at top/)).toBeInTheDocument());
    // 3 prepended mobile cards, not desktop rows
    expect(MOBILE_LOG_ROW_HEIGHT).not.toBe(DESKTOP_LOG_ROW_HEIGHT);
    await waitFor(() => expect(top).toBe(200 + 3 * MOBILE_LOG_ROW_HEIGHT));
  });

  describe('live rows while the user scrolls', () => {
    const useMobileLayout = () => {
      window.matchMedia = ((query: string) => ({
        matches: query.includes('max-width'),
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })) as any;
    };

    // A scroll container whose scrollTop writes are recorded; the reader starts 200 px down (paused)
    const setupPausedScroller = () => {
      const scroller = document.querySelector('.overflow-y-auto') as HTMLElement;
      const state = { top: 200, writes: [] as number[] };
      Object.defineProperty(scroller, 'scrollTop', {
        get: () => state.top,
        set: (v: number) => { state.top = v; state.writes.push(v); },
        configurable: true,
      });
      fireEvent.scroll(scroller);
      expect(screen.getByText('Paused')).toBeInTheDocument();
      return { scroller, state };
    };

    const emitLive = (ids: number[]) => {
      const es = MockEventSource.instances[MockEventSource.instances.length - 1];
      act(() => {
        ids.forEach((id) => es.emit('log', { ...sampleLogs[0], id, message: `Held live ${id}` }));
      });
    };

    const shownLiveRows = () => screen.queryAllByText(/^Held live \d+$/).map((el) => el.textContent);

    it('holds new rows while a finger is down and adds them with one correction after it lifts', async () => {
      useMobileLayout();
      renderStream();
      await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
      await waitFor(() => expect(screen.queryByText('TIMESTAMP')).toBeNull());
      const { scroller, state } = setupPausedScroller();

      vi.useFakeTimers();
      try {
        fireEvent.touchStart(scroller);
        // Three logs over a second and a half of dragging: each batch flushes, none may move the list
        for (const id of [901, 902, 903]) {
          emitLive([id]);
          await act(async () => {
            await vi.advanceTimersByTimeAsync(500);
          });
        }
        expect(state.writes).toEqual([]);
        expect(shownLiveRows()).toEqual([]);
        expect(screen.getByText(/3 new logs at top/)).toBeInTheDocument();

        fireEvent.touchEnd(scroller);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(100);
        });
        // Not yet: a glide may still follow the lift
        expect(state.writes).toEqual([]);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(100);
        });
      } finally {
        vi.useRealTimers();
      }
      expect(state.writes).toEqual([200 + 3 * MOBILE_LOG_ROW_HEIGHT]);
      expect(shownLiveRows()).toEqual(['Held live 903', 'Held live 902', 'Held live 901']);
      expect(screen.getByText(/3 new logs at top/)).toBeInTheDocument();
    });

    it('holds new rows during a glide after the finger lifts', async () => {
      useMobileLayout();
      renderStream();
      await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
      await waitFor(() => expect(screen.queryByText('TIMESTAMP')).toBeNull());
      const { scroller, state } = setupPausedScroller();

      vi.useFakeTimers();
      try {
        fireEvent.touchStart(scroller);
        fireEvent.touchEnd(scroller);
        // The list keeps gliding: a scroll event every 50 ms for 600 ms while logs arrive
        for (let i = 0; i < 12; i++) {
          if (i % 4 === 0) emitLive([910 + i]);
          state.top += 10;
          fireEvent.scroll(scroller);
          await act(async () => {
            await vi.advanceTimersByTimeAsync(50);
          });
        }
        expect(state.writes).toEqual([]);
        expect(shownLiveRows()).toEqual([]);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(200);
        });
      } finally {
        vi.useRealTimers();
      }
      expect(state.writes).toEqual([320 + 3 * MOBILE_LOG_ROW_HEIGHT]);
      expect(shownLiveRows()).toEqual(['Held live 918', 'Held live 914', 'Held live 910']);
    });

    it('keeps auto-scroll on while the Paused button scrolls back to the top as logs arrive', async () => {
      renderStream();
      await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
      const { scroller, state } = setupPausedScroller();
      const scrollTo = vi.fn();
      (scroller as any).scrollTo = scrollTo;

      fireEvent.click(screen.getByText('Paused'));
      expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' });
      expect(screen.getByText('Auto-Scroll ON')).toBeInTheDocument();

      // The smooth scroll is half way up when the next log arrives
      state.top = 100;
      fireEvent.scroll(scroller);
      expect(screen.getByText('Auto-Scroll ON')).toBeInTheDocument();
      state.writes = [];
      emitLive([920]);
      await waitFor(() => expect(screen.getByText('Held live 920')).toBeInTheDocument());
      // The new row snaps the list to the top instead of pushing the reader down
      await waitFor(() => expect(state.writes.length).toBeGreaterThan(0));
      expect(state.writes.every((v) => v === 0)).toBe(true);
      expect(screen.getByText('Auto-Scroll ON')).toBeInTheDocument();
    });

    it('lets the user pause again by scrolling down after the scroll to the top', async () => {
      renderStream();
      await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
      const { scroller, state } = setupPausedScroller();
      (scroller as any).scrollTo = vi.fn();

      fireEvent.click(screen.getByText('Paused'));
      state.top = 0;
      fireEvent.scroll(scroller);
      state.top = 150;
      fireEvent.scroll(scroller);
      expect(screen.getByText('Paused')).toBeInTheDocument();
    });

    it('lets a finger take over from the scroll to the top', async () => {
      renderStream();
      await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
      const { scroller, state } = setupPausedScroller();
      (scroller as any).scrollTo = vi.fn();

      fireEvent.click(screen.getByText('Paused'));
      fireEvent.touchStart(scroller);
      state.top = 150;
      fireEvent.scroll(scroller);
      expect(screen.getByText('Paused')).toBeInTheDocument();
    });
  });

  it('keys virtual rows by log id so prepending a batch keeps existing rows', async () => {
    renderStream();
    await waitFor(() => expect(screen.getByText('Nginx upstream connection timeout')).toBeInTheDocument());
    const getItemKey = virtualizerControl.lastOptions.getItemKey;
    expect(typeof getItemKey).toBe('function');
    expect(getItemKey(0)).toBe(101);
    expect(getItemKey(3)).toBe(104);
  });

  it('aborts a superseded log request when a new one starts', async () => {
    const signals: AbortSignal[] = [];
    vi.spyOn(logsApi, 'fetchLogs').mockImplementation((_params: any, options?: { signal?: AbortSignal }) => {
      if (options?.signal) signals.push(options.signal);
      return new Promise(() => {});
    });
    renderStream();
    await waitFor(() => expect(signals.length).toBe(1));
    fireEvent.click(screen.getByLabelText('Search now'));
    await waitFor(() => expect(signals.length).toBe(2));
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
  });
});
