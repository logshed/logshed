import React, { useEffect, useState } from 'react';
import { fetchHealth } from '../../api/system.ts';
import { HealthResponse } from '../../types.ts';
import { LogShedLogo } from '../common/LogShedLogo.tsx';
import { RecoveryScreen } from './RecoveryScreen.tsx';

/** Health statuses that mean the database upgrade is running or has stopped (everything else is the app). */
export function isUpgradeStatus(status: string | undefined): boolean {
  return status === 'upgrading' || status === 'migration_failed';
}

/**
 * Asks /api/health once before the app starts. After a failed database upgrade (or while one is
 * retried) it shows the recovery screen instead of the app, whose other requests would get 503.
 */
export const StartupGate: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [checked, setChecked] = useState<boolean>(false);

  useEffect(() => {
    fetchHealth()
      .then(setHealth)
      .catch(() => setHealth(null))
      .finally(() => setChecked(true));
  }, []);

  if (!checked) {
    return (
      <div className="min-h-screen bg-canvas flex flex-col items-center justify-center text-fg-muted font-mono text-xs space-y-3">
        <LogShedLogo className="w-8 h-8 text-accent-500 animate-pulse" />
        <span>Loading LogShed...</span>
      </div>
    );
  }
  if (health && isUpgradeStatus(health.status)) {
    return <RecoveryScreen initialHealth={health} />;
  }
  return <>{children}</>;
};

export default StartupGate;
