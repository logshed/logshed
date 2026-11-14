import { useCallback, useEffect, useRef, useState } from 'react';

// How long the Console's high-load note stays after the stream last said it skipped entries
export const SKIPPED_NOTE_MS = 10000;

/**
 * The live updates the server skipped for this Console because it fell behind (the stream's
 * 'skipped' events), added up while the note shows; the count clears SKIPPED_NOTE_MS after the
 * last one, or at once with reset (a new stream).
 */
export const useSkippedNote = () => {
  const [skipped, setSkipped] = useState(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const noteSkipped = useCallback((count: number) => {
    if (!(count > 0)) return;
    setSkipped((n) => n + count);
    clearTimer();
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      setSkipped(0);
    }, SKIPPED_NOTE_MS);
  }, []);

  const reset = useCallback(() => {
    clearTimer();
    setSkipped(0);
  }, []);

  useEffect(() => clearTimer, []);

  return { skipped, noteSkipped, reset };
};
