import { renderHook, act, render, screen, fireEvent } from '@testing-library/react';
import { SlideOver } from '../components/common/SlideOver.tsx';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useClipboard, useFlash, usePolling } from '../utils/hooks.ts';
import { usePullToRefresh } from '../utils/usePullToRefresh.ts';
import * as clipboardModule from '../utils/clipboard.ts';

describe('Custom Hooks (useClipboard)', () => {
  describe('useClipboard', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.restoreAllMocks();
      vi.useRealTimers();
    });

    it('initializes with copied=false', () => {
      const { result } = renderHook(() => useClipboard());
      expect(result.current.copied).toBe(false);
    });

    it('copies text and sets copied=true, resetting after timeout', async () => {
      vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
      const { result } = renderHook(() => useClipboard(1500));

      let promise: Promise<boolean>;
      await act(async () => {
        promise = result.current.copy('test text');
        await promise;
      });

      expect(clipboardModule.copyToClipboard).toHaveBeenCalledWith('test text');
      expect(result.current.copied).toBe(true);

      act(() => {
        vi.advanceTimersByTime(1499);
      });
      expect(result.current.copied).toBe(true);

      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(result.current.copied).toBe(false);
    });

    it('does not set copied=true if copyToClipboard fails', async () => {
      vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(false);
      const { result } = renderHook(() => useClipboard());

      await act(async () => {
        await result.current.copy('test text');
      });

      expect(result.current.copied).toBe(false);
    });

    it('handles empty string gracefully without copying', async () => {
      const spy = vi.spyOn(clipboardModule, 'copyToClipboard');
      const { result } = renderHook(() => useClipboard());

      let ok: boolean = true;
      await act(async () => {
        ok = await result.current.copy('');
      });

      expect(ok).toBe(false);
      expect(spy).not.toHaveBeenCalled();
      expect(result.current.copied).toBe(false);
    });

    it('clears timeout on unmount', async () => {
      vi.spyOn(clipboardModule, 'copyToClipboard').mockResolvedValue(true);
      const { result, unmount } = renderHook(() => useClipboard(2000));

      await act(async () => {
        await result.current.copy('test');
      });
      expect(result.current.copied).toBe(true);

      unmount();
      // Should not throw or cause issues when timers advance after unmount
      act(() => {
        vi.advanceTimersByTime(2000);
      });
    });
  });

  describe('useFlash', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('shows a flashed value for its duration, then clears it', () => {
      const { result } = renderHook(() => useFlash<string>(3000));
      expect(result.current[0]).toBeNull();
      act(() => result.current[1]('Saved'));
      expect(result.current[0]).toBe('Saved');
      act(() => {
        vi.advanceTimersByTime(2999);
      });
      expect(result.current[0]).toBe('Saved');
      act(() => {
        vi.advanceTimersByTime(1);
      });
      expect(result.current[0]).toBeNull();
    });

    it('takes a longer duration for one flash', () => {
      const { result } = renderHook(() => useFlash<string>(3000));
      act(() => result.current[1]('Key cleared', 10000));
      act(() => {
        vi.advanceTimersByTime(9000);
      });
      expect(result.current[0]).toBe('Key cleared');
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(result.current[0]).toBeNull();
    });

    it('never lets an older timer clear a newer value', () => {
      const { result } = renderHook(() => useFlash<string>(3000));
      act(() => result.current[1]('First'));
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      act(() => result.current[1]('Second'));
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      expect(result.current[0]).toBe('Second');

      // A value set without a timer (an error) stays
      act(() => result.current[2]('Error'));
      act(() => {
        vi.advanceTimersByTime(10000);
      });
      expect(result.current[0]).toBe('Error');
    });

    it('clears its timer on unmount', () => {
      const { result, unmount } = renderHook(() => useFlash<string>(3000));
      act(() => result.current[1]('Saved'));
      unmount();
      expect(vi.getTimerCount()).toBe(0);
    });
  });

  describe('usePullToRefresh', () => {
    it('initializes with default state', () => {
      const onRefresh = vi.fn();
      const { result } = renderHook(() => usePullToRefresh({ onRefresh }));

      expect(result.current.pullDistance).toBe(0);
      expect(result.current.isPulling).toBe(false);
      expect(result.current.hasReachedThreshold).toBe(false);
      expect(result.current.isRefreshing).toBe(false);
    });

    it('tracks downward touch drag and calculates pull distance', () => {
      const onRefresh = vi.fn();
      const { result } = renderHook(() => usePullToRefresh({ onRefresh, threshold: 50 }));

      act(() => {
        result.current.touchHandlers.onTouchStart({
          touches: [{ clientX: 0, clientY: 100 }],
        } as any);
      });

      act(() => {
        result.current.touchHandlers.onTouchMove({
          touches: [{ clientX: 0, clientY: 180 }],
        } as any);
      });

      expect(result.current.isPulling).toBe(true);
      expect(result.current.pullDistance).toBeGreaterThan(0);
    });

    it('triggers onRefresh when released past threshold', async () => {
      const onRefresh = vi.fn().mockResolvedValue(undefined);
      const { result } = renderHook(() => usePullToRefresh({ onRefresh, threshold: 40 }));

      act(() => {
        result.current.touchHandlers.onTouchStart({
          touches: [{ clientX: 0, clientY: 100 }],
        } as any);
      });

      act(() => {
        result.current.touchHandlers.onTouchMove({
          touches: [{ clientX: 0, clientY: 250 }],
        } as any);
      });

      expect(result.current.hasReachedThreshold).toBe(true);

      await act(async () => {
        await result.current.touchHandlers.onTouchEnd();
      });

      expect(onRefresh).toHaveBeenCalledTimes(1);
    });

    it('does not trigger onRefresh when released before threshold', async () => {
      const onRefresh = vi.fn();
      const { result } = renderHook(() => usePullToRefresh({ onRefresh, threshold: 60 }));

      act(() => {
        result.current.touchHandlers.onTouchStart({
          touches: [{ clientX: 0, clientY: 100 }],
        } as any);
      });

      act(() => {
        result.current.touchHandlers.onTouchMove({
          touches: [{ clientX: 0, clientY: 120 }], // only 20px
        } as any);
      });

      expect(result.current.hasReachedThreshold).toBe(false);

      await act(async () => {
        await result.current.touchHandlers.onTouchEnd();
      });

      expect(onRefresh).not.toHaveBeenCalled();
      expect(result.current.pullDistance).toBe(0);
    });
  });
});


describe('usePullToRefresh only reacts to a deliberate downward pull', () => {
  const touch = (x: number, y: number) => ({ touches: [{ clientX: x, clientY: y }] }) as any;

  const startGesture = (threshold = 55) => {
    const onRefresh = vi.fn();
    const hook = renderHook(() => usePullToRefresh({ onRefresh, threshold }));
    act(() => hook.result.current.touchHandlers.onTouchStart(touch(100, 100)));
    return { hook, onRefresh };
  };

  it('ignores a small downward drift', () => {
    const { hook } = startGesture();
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(100, 101)));
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(100, 110)));
    expect(hook.result.current.isPulling).toBe(false);
  });

  it('starts the indicator from zero once past the dead zone', () => {
    const { hook } = startGesture();
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(100, 116)));
    expect(hook.result.current.pullDistance).toBe(0);
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(100, 140)));
    expect(hook.result.current.isPulling).toBe(true);
  });

  it('never pulls during a sideways swipe that drifts down later', async () => {
    const { hook, onRefresh } = startGesture();
    // Scrolling the chips to the left
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(60, 104)));
    // The finger then slides well down while still on the bar
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(40, 400)));
    expect(hook.result.current.isPulling).toBe(false);
    await act(async () => {
      await hook.result.current.touchHandlers.onTouchEnd();
    });
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('ignores a diagonal swipe', () => {
    const { hook } = startGesture();
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(140, 160)));
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(160, 300)));
    expect(hook.result.current.isPulling).toBe(false);
  });

  it('pulls and refreshes for a mostly vertical drag with a little sideways movement', async () => {
    const { hook, onRefresh } = startGesture();
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(106, 130)));
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(112, 300)));
    expect(hook.result.current.hasReachedThreshold).toBe(true);
    await act(async () => {
      await hook.result.current.touchHandlers.onTouchEnd();
    });
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('decides afresh for the next gesture', async () => {
    const { hook } = startGesture();
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(40, 100)));
    await act(async () => {
      await hook.result.current.touchHandlers.onTouchEnd();
    });
    act(() => hook.result.current.touchHandlers.onTouchStart(touch(100, 100)));
    act(() => hook.result.current.touchHandlers.onTouchMove(touch(100, 200)));
    expect(hook.result.current.isPulling).toBe(true);
  });
});

describe('usePullToRefresh leaves dialogs and open lists alone', () => {
  /** A header with the pull handlers, as the Navbar and Console toolbar have, holding a drawer and a list. */
  function Header({ onRefresh, drawerOpen }: { onRefresh: () => void; drawerOpen: boolean }) {
    const { touchHandlers } = usePullToRefresh({ onRefresh, threshold: 40 });
    return (
      <div {...touchHandlers}>
        <span>Toolbar</span>
        <ul role="listbox" aria-label="Hosts">
          <li role="option" aria-selected={false}>nas</li>
        </ul>
        <SlideOver isOpen={drawerOpen} onClose={() => {}} title="Filter Logs">
          <span>Drawer content</span>
        </SlideOver>
      </div>
    );
  }

  const pull = async (el: Element) => {
    fireEvent.touchStart(el, { touches: [{ clientX: 100, clientY: 100 }] });
    fireEvent.touchMove(el, { touches: [{ clientX: 100, clientY: 200 }] });
    fireEvent.touchMove(el, { touches: [{ clientX: 100, clientY: 400 }] });
    await act(async () => {
      fireEvent.touchEnd(el);
    });
  };

  it('refreshes for a pull on the header itself', async () => {
    const onRefresh = vi.fn();
    render(<Header onRefresh={onRefresh} drawerOpen={false} />);
    await pull(screen.getByText('Toolbar'));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('does not refresh for a downward swipe inside an open drawer, or on the header while a dialog is open', async () => {
    const onRefresh = vi.fn();
    render(<Header onRefresh={onRefresh} drawerOpen />);
    await pull(screen.getByText('Drawer content'));
    await pull(screen.getByTestId('slideover-backdrop'));
    await pull(screen.getByText('Toolbar'));
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('does not refresh for a downward swipe in an open dropdown list', async () => {
    const onRefresh = vi.fn();
    render(<Header onRefresh={onRefresh} drawerOpen={false} />);
    await pull(screen.getByRole('option', { name: 'nas' }));
    expect(onRefresh).not.toHaveBeenCalled();
  });
});

describe('usePolling', () => {
  let visibility: DocumentVisibilityState = 'visible';

  beforeEach(() => {
    vi.useFakeTimers();
    visibility = 'visible';
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  const setVisibility = (state: DocumentVisibilityState) => {
    visibility = state;
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
  };

  it('polls every interval, not at mount', () => {
    const poll = vi.fn();
    renderHook(() => usePolling(poll, 10000));
    expect(poll).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(30000);
    });
    expect(poll).toHaveBeenCalledTimes(3);
  });

  it('stops while the tab is hidden and polls at once when it is shown again', () => {
    const poll = vi.fn();
    renderHook(() => usePolling(poll, 10000));
    setVisibility('hidden');
    act(() => {
      vi.advanceTimersByTime(60000);
    });
    expect(poll).not.toHaveBeenCalled();

    setVisibility('visible');
    expect(poll).toHaveBeenCalledTimes(1);
    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(poll).toHaveBeenCalledTimes(2);
  });

  it('does not start while mounted in a hidden tab', () => {
    visibility = 'hidden';
    const poll = vi.fn();
    renderHook(() => usePolling(poll, 10000));
    act(() => {
      vi.advanceTimersByTime(30000);
    });
    expect(poll).not.toHaveBeenCalled();
  });

  it('keeps polling while hidden when pauseWhenHidden is off', () => {
    const poll = vi.fn();
    renderHook(() => usePolling(poll, 10000, { pauseWhenHidden: false }));
    setVisibility('hidden');
    act(() => {
      vi.advanceTimersByTime(20000);
    });
    expect(poll).toHaveBeenCalledTimes(2);
  });

  it('calls the latest poll function and stops at unmount', () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender, unmount } = renderHook(({ fn }) => usePolling(fn, 10000), {
      initialProps: { fn: first },
    });
    rerender({ fn: second });
    act(() => {
      vi.advanceTimersByTime(10000);
    });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);

    unmount();
    setVisibility('hidden');
    setVisibility('visible');
    act(() => {
      vi.advanceTimersByTime(30000);
    });
    expect(second).toHaveBeenCalledTimes(1);
  });
});
