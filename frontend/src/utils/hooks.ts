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
