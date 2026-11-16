import React from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts';
import { TrendingUp } from 'lucide-react';
import { StorageMetricsSnapshot } from '../../types.ts';
import { Card, CardHeader } from '../common/Card.tsx';
import { cleanIsoString, formatBytes } from '../../utils/formatters.ts';
import { useServerTimezone } from '../../utils/hooks.ts';
import { HOUR_MS, buildTimeTicks, formatTimePoint, formatTimeTick } from '../../utils/timeAxis.ts';

interface StorageTrendChartProps {
  history: StorageMetricsSnapshot[];
}

// The chart's snapshots are hourly, so ticks are at least an hour apart
const MIN_TICK_STEP_MS = HOUR_MS;

export const StorageTrendChart: React.FC<StorageTrendChartProps> = ({ history }) => {
  const serverTimezone = useServerTimezone();
  if (!history || history.length === 0) {
    return (
      <Card>
        <CardHeader icon={<TrendingUp />} title="30-Day Storage & Volume Trend" />
        <div className="h-48 flex items-center justify-center text-fg-muted font-mono text-xs">
          No historical storage metrics recorded yet. Metrics are sampled hourly.
        </div>
      </Card>
    );
  }

  // Plot snapshots by time: besides the hourly samples, log deletion and compaction record
  // extra ones, which an evenly spaced category axis would show as repeated hour labels
  const chartData = history
    .map((item) => ({
      time: new Date(cleanIsoString(item.recorded_at)).getTime(),
      dbMb: parseFloat((item.db_size_bytes / (1024 * 1024)).toFixed(2)),
      dbBytes: item.db_size_bytes,
      logsCount: item.total_logs_count,
    }))
    .filter((item) => !isNaN(item.time))
    .sort((a, b) => a.time - b.time);

  const minTime = chartData.length ? chartData[0].time : 0;
  const maxTime = chartData.length ? chartData[chartData.length - 1].time : 0;
  const { ticks, stepMs } = buildTimeTicks(minTime, maxTime, serverTimezone, { minStepMs: MIN_TICK_STEP_MS });
  // A single snapshot has no range, so pad the domain to keep the point on the chart
  const domain = maxTime > minTime ? [minTime, maxTime] : [minTime - HOUR_MS / 2, maxTime + HOUR_MS / 2];

  return (
    <Card>
      <CardHeader
        icon={<TrendingUp />}
        title="30-Day Storage & Volume Trend"
        actions={<span className="text-[11px] font-mono text-fg-muted">Hourly Snapshots</span>}
      />

      <div className="h-56 w-full" data-testid="storage-trend-chart">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="dbSizeGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--color-accent-500)" stopOpacity={0.4} />
                <stop offset="95%" stopColor="var(--color-accent-500)" stopOpacity={0.0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-line)" />
            <XAxis
              dataKey="time"
              type="number"
              scale="time"
              domain={domain}
              ticks={ticks}
              tickFormatter={(val: number) => formatTimeTick(val, stepMs, serverTimezone)}
              stroke="var(--color-fg-subtle)"
              tick={{ fontSize: 10 }}
            />
            <YAxis
              stroke="var(--color-fg-subtle)"
              tick={{ fontSize: 10 }}
              width={70}
              tickFormatter={(val: number) => (val >= 10 ? Math.round(val).toLocaleString() : String(val))}
              unit="MB"
            />
            <Tooltip
              contentStyle={{
                backgroundColor: 'var(--color-surface)',
                borderColor: 'var(--color-line-strong)',
                borderRadius: '8px',
                fontSize: '11px',
                fontFamily: 'monospace',
              }}
              labelFormatter={(val: any) => formatTimePoint(Number(val), serverTimezone)}
              formatter={(value: any, name: any, item: any) => {
                if (name === 'dbMb') {
                  const dbBytes = item?.payload?.dbBytes;
                  return [
                    typeof dbBytes === 'number' ? formatBytes(dbBytes) : `${Number(value).toLocaleString()} MB`,
                    'Database Size',
                  ];
                }
                return [value, name];
              }}
            />
            <Area
              type="monotone"
              dataKey="dbMb"
              stroke="var(--color-accent-500)"
              strokeWidth={2}
              fillOpacity={1}
              fill="url(#dbSizeGrad)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
};
