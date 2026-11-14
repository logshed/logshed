import { useEffect, useState } from 'react';

// Why LogShed is near its processing limit, as the Navbar's health dot works it out from each
// health poll ('loop': the event loop has been late for a while; 'queue': logs arrive faster than
// they are stored), or null. The Navbar announces it here so the Console can say what it means
// for the live view without polling the health endpoint a second time.
export type NearLimitReason = 'loop' | 'queue' | 'both';

let current: NearLimitReason | null = null;
const listeners = new Set<(reason: NearLimitReason | null) => void>();

export const announceNearLimit = (reason: NearLimitReason | null): void => {
  if (reason === current) return;
  current = reason;
  listeners.forEach((listener) => listener(reason));
};

export const useNearLimit = (): NearLimitReason | null => {
  const [reason, setReason] = useState<NearLimitReason | null>(current);
  useEffect(() => {
    // The value may have changed between the first render and this effect
    setReason(current);
    listeners.add(setReason);
    return () => {
      listeners.delete(setReason);
    };
  }, []);
  return reason;
};
