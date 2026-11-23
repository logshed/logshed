import { MONTH_NAMES, getDateParts, isKnownTimeZone, wallClockMs, zoneWallClockToEpoch } from './formatters.ts';

// Time axes for charts, on the server's clock as every other time in the web UI. Ticks sit at
// even wall-clock times of that zone (06:00, midnight), whatever zone the browser is in.

const MINUTE_MS = 60 * 1000;
export const HOUR_MS = 60 * MINUTE_MS;
export const DAY_MS = 24 * HOUR_MS;

// Tick spacing candidates; the smallest that keeps the tick count within maxTicks wins. Every
// step under a day divides a day evenly, so ticks line up with the zone's midnight.
const TICK_STEPS_MS = [
  ...[1, 2, 5, 10, 15, 30].map((seconds) => seconds * 1000),
  ...[1, 2, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 10080].map((minutes) => minutes * MINUTE_MS),
];

export interface TimeTicks {
  ticks: number[];
  /** Spacing of the ticks, which decides the label format. */
  stepMs: number;
}

/** The zone a chart axis uses: the server zone, or the browser's when the server's is unknown. */
export function axisTimeZone(timeZone?: string): string {
  if (isKnownTimeZone(timeZone)) return timeZone;
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

/**
 * Axis ticks between min and max (epoch milliseconds) at even wall-clock times of the zone. Steps
 * of a day or more start at the zone's midnight on min's day. Ticks are counted on the zone's
 * clock, so they stay on the hour across daylight saving changes; a wall-clock time the clock
 * skips gets no tick.
 */
export function buildTimeTicks(
  min: number,
  max: number,
  timeZone?: string,
  { maxTicks = 7, minStepMs = 0 }: { maxTicks?: number; minStepMs?: number } = {},
): TimeTicks {
  const candidates = TICK_STEPS_MS.filter((step) => step >= minStepMs);
  if (!(max > min)) return { ticks: [min], stepMs: candidates[0] ?? HOUR_MS };
  const span = max - min;
  const stepMs = candidates.find((step) => span / step <= maxTicks) ?? candidates[candidates.length - 1];

  const zone = axisTimeZone(timeZone);
  const minWall = wallClockMs(min, zone);
  const unit = stepMs >= DAY_MS ? DAY_MS : stepMs;
  let wall = Math.floor(minWall / unit) * unit;
  const ticks: number[] = [];
  // The guard only matters for a broken zone; a valid range never needs more than a few dozen steps
  for (let guard = 0; guard < 1000; guard++, wall += stepMs) {
    const epoch = zoneWallClockToEpoch(wall, zone);
    if (epoch > max) break;
    if (epoch >= min && wallClockMs(epoch, zone) === wall) ticks.push(epoch);
  }
  return { ticks, stepMs };
}

const pad2 = (n: number | string) => String(n).padStart(2, '0');

/**
 * Axis label on the zone's clock: 'D Mon' for day steps, 'D Mon HH:00' for hour steps, 'HH:mm' below
 * an hour and 'HH:mm:ss' below a minute. withDate: false leaves the date off hour steps too, for
 * ranges inside a day.
 */
export function formatTimeTick(ms: number, stepMs: number, timeZone?: string, withDate = true): string {
  const p = getDateParts(new Date(ms), axisTimeZone(timeZone));
  const day = `${p.day} ${MONTH_NAMES[p.month - 1]}`;
  if (stepMs >= DAY_MS) return day;
  if (stepMs >= HOUR_MS) return withDate ? `${day} ${p.hours}:00` : `${p.hours}:00`;
  if (stepMs >= MINUTE_MS) return `${p.hours}:${pad2(p.minutes)}`;
  return `${p.hours}:${pad2(p.minutes)}:${pad2(p.seconds)}`;
}

/** A point in time for a chart tooltip: 'D Mon HH:mm' on the zone's clock, with ':ss' when withSeconds. */
export function formatTimePoint(ms: number, timeZone?: string, withSeconds = false): string {
  const p = getDateParts(new Date(ms), axisTimeZone(timeZone));
  return `${p.day} ${MONTH_NAMES[p.month - 1]} ${p.hours}:${p.minutes}${withSeconds ? `:${p.seconds}` : ''}`;
}
