import { UpgradeStatus } from '../../types.ts';
import { formatBytes } from '../../utils/formatters.ts';

/** What a running database upgrade is doing, as the server's status page words it. */
export function upgradeProgressText(upgrade: UpgradeStatus | null | undefined): string {
  if (!upgrade) return 'Upgrading the database';
  const done = upgrade.done ?? 0;
  const total = upgrade.total ?? 0;
  if (upgrade.stage === 'backup') {
    if (total > 0) {
      return `Backing up the database: ${formatBytes(done)} of ${formatBytes(total)} (${Math.floor((done * 100) / total)}%)`;
    }
    return 'Backing up the database';
  }
  if (upgrade.stage === 'migrate') {
    const step = total > 0 ? ` (step ${Math.min(done + 1, total)} of ${total})` : '';
    return `Upgrading the database from schema ${upgrade.from_schema} to ${upgrade.target_schema}${step}`;
  }
  return 'Starting LogShed';
}
