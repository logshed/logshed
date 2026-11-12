import { useState, useEffect, useRef, useCallback } from 'react';
import { copyToClipboard } from './clipboard.ts';
import { fetchSettings, getCachedServerTimezone } from '../api/settings.ts';

export interface UseClipboardReturn {
  copied: boolean;
  copy: (text: string) => Promise<boolean>;
}

/**
 * Reusable clipboard hook that manages copied state and timeout cleanup.
 */
export function useClipboard(timeoutMs: number = 2000): UseClipboardReturn {
  const [copied, setCopied] = useState<boolean>(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const copy = useCallback(
    async (text: string): Promise<boolean> => {
      if (!text) return false;
      const ok = await copyToClipboard(text);
      if (ok) {
        if (timeoutRef.current !== null) {
          clearTimeout(timeoutRef.current);
        }
        setCopied(true);
        timeoutRef.current = setTimeout(() => {
          setCopied(false);
          timeoutRef.current = null;
        }, timeoutMs);
      }
      return ok;
    },
    [timeoutMs]
  );

  useEffect(() => {
    return () => {
      if (timeoutRef.current !== null) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  return { copied, copy };
}

/**
 * A value shown for a moment, such as a "Saved" mark or a success message.
 * flash(value) shows it and clears it after durationMs (or the ms given); set(value) shows it
 * until replaced, for errors that stay. Each call replaces the pending clear, so an older timer
 * never hides a newer value, and the timer is cleared on unmount.
 */
export function useFlash<T>(
  durationMs: number
): [T | null, (value: T, ms?: number) => void, (value: T | null) => void] {
  const [value, setValue] = useState<T | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancel = useCallback(() => {
    if (timeoutRef.current !== null) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  const set = useCallback(
    (next: T | null) => {
      cancel();
      setValue(next);
    },
    [cancel]
  );

  const flash = useCallback(
    (next: T, ms: number = durationMs) => {
      cancel();
      setValue(next);
      timeoutRef.current = setTimeout(() => {
        timeoutRef.current = null;
        setValue(null);
      }, ms);
    },
    [cancel, durationMs]
  );

  useEffect(() => cancel, [cancel]);

  return [value, flash, set];
}

/**
 * Hook to evaluate a CSS media query and react to viewport resize / orientation changes.
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState<boolean>(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const mediaQueryList = window.matchMedia(query);
    const listener = (event: MediaQueryListEvent) => {
      setMatches(event.matches);
    };
    setMatches(mediaQueryList.matches);
    mediaQueryList.addEventListener('change', listener);
    return () => {
      mediaQueryList.removeEventListener('change', listener);
    };
  }, [query]);

  return matches;
}


/**
 * The server's configured time zone, for showing times the way the log stream does.
 * Starts from the cached settings and is undefined (browser time) until settings load.
 */
export function useServerTimezone(): string | undefined {
  const [serverTimezone, setServerTimezone] = useState<string | undefined>(() => getCachedServerTimezone());
  useEffect(() => {
    let active = true;
    fetchSettings()
      .then((s) => {
        if (active && s.server_timezone) setServerTimezone(s.server_timezone);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  return serverTimezone;
}

/**
 * Calls poll every intervalMs while mounted. With pauseWhenHidden (the default), polling stops while
 * the browser tab is hidden and runs at once when it is shown again, so a background tab sends no
 * requests and a returning user sees fresh data. Always calls the latest poll function.
 */
export function usePolling(
  poll: () => void,
  intervalMs: number,
  { pauseWhenHidden = true }: { pauseWhenHidden?: boolean } = {}
): void {
  const pollRef = useRef(poll);
  pollRef.current = poll;

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer === null) timer = setInterval(() => pollRef.current(), intervalMs);
    };
    const stop = () => {
      if (timer !== null) clearInterval(timer);
      timer = null;
    };
    const isHidden = () => pauseWhenHidden && document.visibilityState === 'hidden';

    if (!isHidden()) start();
    if (!pauseWhenHidden) return stop;

    const onVisibilityChange = () => {
      if (isHidden()) {
        stop();
      } else if (timer === null) {
        pollRef.current();
        start();
      }
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [intervalMs, pauseWhenHidden]);
}
