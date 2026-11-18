import { UpgradeStatus } from '../../types.ts';
import { formatBytes } from '../../utils/formatters.ts';

/** A schema as users read it: "LogShed 1.3.x", or "schema N" when the server names no release for it. */
export function releaseLabel(release: string | null | undefined, schema: number | null | undefined): string {
  return release ? `LogShed ${release}` : `schema ${schema ?? '?'}`;
}

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
    const from = releaseLabel(upgrade.from_release, upgrade.from_schema);
    return `Upgrading the database from ${from} to ${releaseLabel(upgrade.target_release, upgrade.target_schema)}${step}`;
  }
  return 'Starting LogShed';
}
