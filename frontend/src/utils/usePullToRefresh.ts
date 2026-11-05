import { useState, useRef, useCallback } from 'react';
import { openDialogCount } from './dialogStack.ts';

export interface PullTouchHandlers {
  onTouchStart: (e: React.TouchEvent) => void;
  onTouchMove: (e: React.TouchEvent) => void;
  onTouchEnd: () => void;
  onTouchCancel: () => void;
}

interface UsePullToRefreshOptions {
  onRefresh: () => Promise<void> | void;
  threshold?: number;
  disabled?: boolean;
}

/** Finger travel (px) before a gesture is read as a pull or as something else. */
export const PULL_DEAD_ZONE_PX = 16;
/** A pull must move at least this many times further down than sideways. */
const PULL_MIN_STEEPNESS = 2;

type GestureMode = 'undecided' | 'pull' | 'ignore';

/** Where a swipe never starts a pull: dialogs, open lists, and popovers marked data-no-pull-refresh. */
const NO_PULL_SELECTOR = '[role="dialog"], [role="listbox"], [data-no-pull-refresh]';

export function usePullToRefresh({
  onRefresh,
  threshold = 60,
  disabled = false,
}: UsePullToRefreshOptions) {
  const [pullDistance, setPullDistance] = useState<number>(0);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);

  const startRef = useRef<{ x: number; y: number } | null>(null);
  const isDraggingRef = useRef<boolean>(false);
  // Decided once per gesture, so a sideways swipe (such as scrolling the quick filter chips)
  // that drifts down later never turns into a pull
  const modeRef = useRef<GestureMode>('undecided');

  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      if (disabled || isRefreshing) return;
      if (e.touches.length !== 1) return;
      // Touch events bubble through the React tree, so a swipe in a drawer or an open list rendered
      // inside the header reaches these handlers too: no pull while a dialog is open or from a list
      if (openDialogCount() > 0) return;
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest(NO_PULL_SELECTOR)) return;
      startRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      modeRef.current = 'undecided';
      isDraggingRef.current = true;
    },
    [disabled, isRefreshing]
  );

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (disabled || isRefreshing || !isDraggingRef.current || startRef.current === null) {
        return;
      }
      if (e.touches.length !== 1) {
        modeRef.current = 'ignore';
        setPullDistance(0);
        return;
      }
      const dx = e.touches[0].clientX - startRef.current.x;
      const dy = e.touches[0].clientY - startRef.current.y;

      if (modeRef.current === 'undecided') {
        if (Math.hypot(dx, dy) < PULL_DEAD_ZONE_PX) return;
        modeRef.current = dy > 0 && dy >= Math.abs(dx) * PULL_MIN_STEEPNESS ? 'pull' : 'ignore';
      }
      if (modeRef.current !== 'pull') return;

      // Measured from the end of the dead zone, so the indicator starts at 0
      const pulled = dy - PULL_DEAD_ZONE_PX;
      if (pulled > 0) {
        // Apply dampening resistance
        const dampened = Math.min(pulled * 0.45, threshold + 25);
        setPullDistance(dampened);
      } else {
        setPullDistance(0);
      }
    },
    [disabled, isRefreshing, threshold]
  );

  const handleTouchEnd = useCallback(async () => {
    if (disabled || isRefreshing || !isDraggingRef.current) return;
    isDraggingRef.current = false;
    startRef.current = null;
    modeRef.current = 'undecided';

    if (pullDistance >= threshold) {
      setIsRefreshing(true);
      setPullDistance(threshold * 0.7);
      try {
        await Promise.resolve(onRefresh());
      } catch {
        // Refresh errors handled gracefully
      } finally {
        setTimeout(() => {
          setIsRefreshing(false);
          setPullDistance(0);
        }, 400);
      }
    } else {
      setPullDistance(0);
    }
  }, [disabled, isRefreshing, pullDistance, threshold, onRefresh]);

  const hasReachedThreshold = pullDistance >= threshold;

  return {
    pullDistance,
    isPulling: pullDistance > 0,
    hasReachedThreshold,
    isRefreshing,
    touchHandlers: {
      onTouchStart: handleTouchStart,
      onTouchMove: handleTouchMove,
      onTouchEnd: handleTouchEnd,
      onTouchCancel: () => {
        isDraggingRef.current = false;
        startRef.current = null;
        modeRef.current = 'undecided';
        setPullDistance(0);
      },
    },
  };
}
