import { SEVERITIES, getSeverityInfo } from '../components/common/SeverityBadge.tsx';

// Short names used by the compact Console select.
const COMPACT_NAMES: Record<number, string> = {
  0: 'Emerg',
  1: 'Alert',
  2: 'Crit',
  3: 'Error',
  4: 'Warn',
  5: 'Notice',
  6: 'Info',
  7: 'Debug',
};

function levelName(n: number): string {
  return SEVERITIES.find((s) => s.value === n)?.name ?? getSeverityInfo(n).label;
}

const MOST_SEVERE = 0;
const LEAST_SEVERE = 7;

/**
 * Console filters and alert rules keep severity <= n. Example: "Warning (4) and more severe";
 * Emergency (0) is that level only, and Debug (7) is every level.
 */
export function moreSevereLabel(n: number): string {
  if (n === MOST_SEVERE) return `${levelName(n)} (${n}) only`;
  if (n === LEAST_SEVERE) return `${levelName(n)} (${n}): every level`;
  return `${levelName(n)} (${n}) and more severe`;
}

/**
 * Drop rules drop severity >= n. Example: "Warning (4) and less severe"; Debug (7) is that level
 * only, and Emergency (0) is every level.
 */
export function lessSevereLabel(n: number): string {
  if (n === LEAST_SEVERE) return `${levelName(n)} (${n}) only`;
  if (n === MOST_SEVERE) return `${levelName(n)} (${n}): every level`;
  return `${levelName(n)} (${n}) and less severe`;
}

/** Short form for narrow selects. Example: "Warn+ (0-4)". Emergency alone reads "Emerg (0)". */
export function compactMoreSevereLabel(n: number): string {
  const name = COMPACT_NAMES[n] ?? levelName(n);
  if (n === 0) return `${name} (0)`;
  return `${name}+ (0-${n})`;
}
