import type React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { StorageTrendChart } from '../components/storage/StorageTrendChart.tsx';
import type { StorageMetricsSnapshot } from '../types.ts';

// A server zone other than the runner's: the axis must follow it, not the browser
vi.mock('../utils/hooks.ts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../utils/hooks.ts')>()),
  useServerTimezone: () => 'Asia/Tokyo',
}));

// Recharts measures nothing in jsdom: the chart gets a fixed size, and the stub axis prints the
// ticks and labels the chart gives it, which is what the server zone decides
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>();
  const { cloneElement } = await import('react');
  const mocked = {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactElement<any> }) =>
      cloneElement(children, { width: 800, height: 220 }),
    XAxis: ({ ticks, tickFormatter }: { ticks: number[]; tickFormatter: (v: number) => string }) => (
      <g data-testid="x-axis">
        {ticks.map((t) => (
          <text key={t}>{tickFormatter(t)}</text>
        ))}
      </g>
    ),
  };
  return { ...mocked, default: mocked };
});

// Tokyo is UTC+9 all year
const tokyo = (day: number, hour: number) => Date.UTC(2026, 9, day, hour) - 9 * 3600_000;

const snapshot = (ms: number): StorageMetricsSnapshot =>
  ({ recorded_at: new Date(ms).toISOString(), db_size_bytes: 10 * 1024 * 1024, total_logs_count: 100 }) as StorageMetricsSnapshot;

describe('StorageTrendChart', () => {
  it('labels the time axis on the server clock', async () => {
    // Hourly snapshots from 20 Oct 16:00 to 21 Oct 12:00 Tokyo time: 3-hour ticks at Tokyo's even hours
    const history = Array.from({ length: 21 }, (_, i) => snapshot(tokyo(20, 16) + i * 3600_000));
    render(<StorageTrendChart history={history} />);
    const axis = await screen.findByTestId('x-axis');
    expect([...axis.querySelectorAll('text')].map((t) => t.textContent)).toEqual(['20 Oct 18:00', '20 Oct 21:00', '21 Oct 00:00', '21 Oct 03:00', '21 Oct 06:00', '21 Oct 09:00', '21 Oct 12:00']);
  });

  it('shows the empty state without snapshots', () => {
    render(<StorageTrendChart history={[]} />);
    expect(screen.getByText(/No historical storage metrics recorded yet/i)).toBeInTheDocument();
  });
});
