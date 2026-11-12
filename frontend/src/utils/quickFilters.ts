import { LogEntry } from '../types.ts';

/** Number of host chips and of app chips shown in the Quick Filters bar. */
export const QUICK_FILTER_CHIP_LIMIT = 6;
/** While live, the chip ranking is refreshed at most this often, so chips do not jump as logs stream in. */
export const QUICK_FILTER_RERANK_MS = 30_000;

export interface QuickFilterCounts {
  hosts: Record<string, number>;
  apps: Record<string, number>;
}

/** Counts the buffer's rows per host (as shown in the Host column) and per app. */
export function countQuickFilterVolumes(logs: LogEntry[]): QuickFilterCounts {
  // No prototype, so host or app names such as "constructor" count like any other
  const hosts: Record<string, number> = Object.create(null);
  const apps: Record<string, number> = Object.create(null);
  for (const log of logs) {
    const host = log.source_alias || log.source_ip;
    if (host) hosts[host] = (hosts[host] || 0) + 1;
    if (log.app_name) apps[log.app_name] = (apps[log.app_name] || 0) + 1;
  }
  return { hosts, apps };
}

/**
 * Picks the Quick Filter chips: the `limit` busiest candidates by buffer volume (ties alphabetical),
 * then any selected value that fell outside them, so a chip in use can always be turned off.
 */
export function rankQuickFilterChips(
  candidates: string[],
  counts: Record<string, number>,
  selected: string[],
  limit: number = QUICK_FILTER_CHIP_LIMIT,
): string[] {
  const countOf = (key: string) => (Object.prototype.hasOwnProperty.call(counts, key) ? counts[key] : 0);
  const ranked = Array.from(new Set(candidates)).sort((a, b) => {
    const diff = countOf(b) - countOf(a);
    if (diff !== 0) return diff;
    return a < b ? -1 : a > b ? 1 : 0;
  });
  const shown = ranked.slice(0, limit);
  const shownSet = new Set(shown);
  for (const value of selected) {
    if (!shownSet.has(value)) {
      shown.push(value);
      shownSet.add(value);
    }
  }
  return shown;
}
