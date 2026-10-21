import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { StorageTrendChart, buildTrendTicks, formatTrendTick } from '../components/storage/StorageTrendChart.tsx';

// Local-time constructors keep these tests independent of the runner's time zone
const local = (month: number, day: number, hour: number, minute = 0) =>
  new Date(2026, month - 1, day, hour, minute).getTime();

describe('StorageTrendChart time axis', () => {
  it('places ticks on even local hours inside the range', () => {
    // 10/20 16:23 to 10/21 12:07 is about 20 hours, so 3-hour steps give at most 7 ticks
    const { ticks, stepHours } = buildTrendTicks(local(10, 20, 16, 23), local(10, 21, 12, 7));
    expect(stepHours).toBe(3);
    expect(ticks.map((t) => formatTrendTick(t, stepHours))).toEqual([
      '10/20 18:00',
      '10/20 21:00',
      '10/21 00:00',
      '10/21 03:00',
      '10/21 06:00',
      '10/21 09:00',
      '10/21 12:00',
    ]);
  });

  it('never repeats a label when extra snapshots fall in the same hour', () => {
    // Ticks come from the time range alone, so deletion and compaction snapshots between the
    // hourly samples cannot add a second '10:00' label
    const { ticks, stepHours } = buildTrendTicks(local(10, 21, 8), local(10, 21, 12));
    const labels = ticks.map((t) => formatTrendTick(t, stepHours));
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels).toEqual(['10/21 08:00', '10/21 09:00', '10/21 10:00', '10/21 11:00', '10/21 12:00']);
  });

  it('switches to day labels at local midnight for long ranges', () => {
    const { ticks, stepHours } = buildTrendTicks(local(9, 22, 14), local(10, 21, 14));
    expect(stepHours).toBe(168);
    const labels = ticks.map((t) => formatTrendTick(t, stepHours));
    expect(labels).toEqual(['9/29', '10/6', '10/13', '10/20']);
    ticks.forEach((t) => expect(new Date(t).getHours()).toBe(0));
  });

  it('returns the single point when the range is empty', () => {
    const point = local(10, 21, 12, 30);
    expect(buildTrendTicks(point, point)).toEqual({ ticks: [point], stepHours: 1 });
  });

  it('shows the empty state without snapshots', () => {
    render(<StorageTrendChart history={[]} />);
    expect(screen.getByText(/No historical storage metrics recorded yet/i)).toBeInTheDocument();
  });
});
