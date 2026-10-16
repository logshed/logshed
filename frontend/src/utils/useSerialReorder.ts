import { useCallback, useEffect, useRef } from 'react';

/**
 * Sends rule reorder requests one at a time.
 *
 * Each move updates the list optimistically right away. While a request is in flight, only the
 * latest desired order is kept and sent when the current request finishes. A failure rolls the
 * list back to the last order the server confirmed (the order from before the burst of moves).
 */
export function useSerialReorder<T extends { id: number }>(
  items: T[],
  setItems: (items: T[]) => void,
  sendOrder: (ids: number[]) => Promise<T[]>,
  onError: (message: string) => void
): (newOrder: T[]) => void {
  const itemsRef = useRef<T[]>(items);
  itemsRef.current = items;
  const confirmedRef = useRef<T[]>(items);
  const pendingRef = useRef<T[] | null>(null);
  const inFlightRef = useRef<boolean>(false);
  const mountedRef = useRef<boolean>(true);
  const sendRef = useRef(sendOrder);
  sendRef.current = sendOrder;
  const errorRef = useRef(onError);
  errorRef.current = onError;
  const setRef = useRef(setItems);
  setRef.current = setItems;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  return useCallback((newOrder: T[]) => {
    if (!inFlightRef.current) {
      confirmedRef.current = itemsRef.current;
    }
    itemsRef.current = newOrder;
    setRef.current(newOrder);
    pendingRef.current = newOrder;
    if (inFlightRef.current) return;

    inFlightRef.current = true;
    void (async () => {
      try {
        while (pendingRef.current) {
          const next = pendingRef.current;
          pendingRef.current = null;
          try {
            const updated = await sendRef.current(next.map((r) => r.id));
            confirmedRef.current = updated;
            if (!pendingRef.current && mountedRef.current) {
              itemsRef.current = updated;
              setRef.current(updated);
            }
          } catch (err: any) {
            pendingRef.current = null;
            if (mountedRef.current) {
              itemsRef.current = confirmedRef.current;
              setRef.current(confirmedRef.current);
              errorRef.current(err?.message || 'Failed to update rule order.');
            }
          }
        }
      } finally {
        inFlightRef.current = false;
      }
    })();
  }, []);
}
