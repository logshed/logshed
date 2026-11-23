import { describe, it, expect } from 'vitest';
import { zoneWallClockToEpoch } from '../utils/formatters.ts';
import { DAY_MS, HOUR_MS, buildTimeTicks, formatTimePoint, formatTimeTick } from '../utils/timeAxis.ts';

// The runner's zone is not one of these, so a tick on the browser's clock would show up as wrong
const NEW_YORK = 'America/New_York';
const KOLKATA = 'Asia/Kolkata';

// The instant at which the zone's clock shows this wall-clock time
const at = (zone: string, month: number, day: number, hour: number, minute = 0, year = 2026) =>
  zoneWallClockToEpoch(Date.UTC(year, month - 1, day, hour, minute), zone);

const labels = (min: number, max: number, zone?: string, options?: Parameters<typeof buildTimeTicks>[3]) => {
  const { ticks, stepMs } = buildTimeTicks(min, max, zone, options);
  return ticks.map((t) => formatTimeTick(t, stepMs, zone));
};

describe('buildTimeTicks on the server clock', () => {
  it('places hour ticks on even hours of the server zone', () => {
    // About 20 hours, so 3-hour steps give at most 7 ticks
    const { ticks, stepMs } = buildTimeTicks(at(NEW_YORK, 10, 20, 16, 23), at(NEW_YORK, 10, 21, 12, 7), NEW_YORK);
    expect(stepMs).toBe(3 * HOUR_MS);
    expect(ticks.map((t) => formatTimeTick(t, stepMs, NEW_YORK))).toEqual([
      '20 Oct 18:00', '20 Oct 21:00', '21 Oct 00:00', '21 Oct 03:00', '21 Oct 06:00', '21 Oct 09:00', '21 Oct 12:00',
    ]);
  });

  it('starts day steps at the server zone midnight, also in a zone a half hour off UTC', () => {
    const { ticks, stepMs } = buildTimeTicks(at(KOLKATA, 9, 22, 14), at(KOLKATA, 10, 21, 14), KOLKATA, { minStepMs: HOUR_MS });
    expect(stepMs).toBe(7 * DAY_MS);
    expect(ticks.map((t) => formatTimeTick(t, stepMs, KOLKATA))).toEqual(['29 Sep', '6 Oct', '13 Oct', '20 Oct']);
    expect(ticks).toEqual([at(KOLKATA, 9, 29, 0), at(KOLKATA, 10, 6, 0), at(KOLKATA, 10, 13, 0), at(KOLKATA, 10, 20, 0)]);
  });

  it('keeps hour ticks on the hour on the day the clock goes forward', () => {
    // New York skips 02:00 to 03:00 on 2026-03-08
    expect(labels(at(NEW_YORK, 3, 8, 0), at(NEW_YORK, 3, 8, 6), NEW_YORK)).toEqual([
      '8 Mar 00:00', '8 Mar 01:00', '8 Mar 03:00', '8 Mar 04:00', '8 Mar 05:00', '8 Mar 06:00',
    ]);
  });

  it('keeps hour ticks on the hour on the day the clock goes back, without a repeated label', () => {
    // New York shows 01:00 to 02:00 twice on 2026-11-01
    const shown = labels(at(NEW_YORK, 11, 1, 0), at(NEW_YORK, 11, 1, 5), NEW_YORK);
    expect(shown).toEqual(['1 Nov 00:00', '1 Nov 01:00', '1 Nov 02:00', '1 Nov 03:00', '1 Nov 04:00', '1 Nov 05:00']);
  });

  it('keeps day ticks at midnight across both daylight saving changes', () => {
    for (const [from, to] of [[[3, 5], [3, 11]], [[10, 29], [11, 4]]] as const) {
      const { ticks, stepMs } = buildTimeTicks(at(NEW_YORK, from[0], from[1], 12), at(NEW_YORK, to[0], to[1], 12), NEW_YORK);
      expect(stepMs).toBe(DAY_MS);
      expect(ticks).toHaveLength(6);
      ticks.forEach((t) => expect(formatTimePoint(t, NEW_YORK)).toMatch(/ 00:00$/));
    }
  });

  it('steps below an hour for short ranges and labels them HH:mm', () => {
    expect(labels(at(NEW_YORK, 10, 20, 16, 3), at(NEW_YORK, 10, 20, 16, 18), NEW_YORK)).toEqual([
      '16:05', '16:10', '16:15',
    ]);
  });

  it('steps in seconds for ranges under a few minutes and labels them HH:mm:ss', () => {
    expect(labels(at(NEW_YORK, 10, 20, 16, 0), at(NEW_YORK, 10, 20, 16, 1), NEW_YORK)).toEqual([
      '16:00:00', '16:00:10', '16:00:20', '16:00:30', '16:00:40', '16:00:50', '16:01:00',
    ]);
  });

  it('leaves the date off hour labels when asked', () => {
    expect(formatTimeTick(at(NEW_YORK, 10, 20, 18), HOUR_MS, NEW_YORK, false)).toBe('18:00');
  });

  it('falls back to browser time when the server zone is unknown', () => {
    const min = new Date(2026, 9, 20, 16, 23).getTime();
    const max = new Date(2026, 9, 21, 12, 7).getTime();
    expect(labels(min, max, 'Not/A_Zone')).toEqual(labels(min, max, undefined));
    expect(buildTimeTicks(min, max, undefined).ticks.every((t) => new Date(t).getHours() % 3 === 0)).toBe(true);
  });

  it('returns the single point when the range is empty', () => {
    const point = at(NEW_YORK, 10, 21, 12, 30);
    expect(buildTimeTicks(point, point, NEW_YORK, { minStepMs: HOUR_MS })).toEqual({ ticks: [point], stepMs: HOUR_MS });
  });

  it('shows tooltip times on the server clock', () => {
    expect(formatTimePoint(at(NEW_YORK, 10, 20, 7, 5), NEW_YORK)).toBe('20 Oct 07:05');
  });
});
