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
import { cleanIsoString, formatBytes } from '../../utils/formatters.ts';

interface StorageTrendChartProps {
  history: StorageMetricsSnapshot[];
}

const HOUR_MS = 3600 * 1000;
// Tick spacing candidates in hours; the smallest that fits MAX_TICKS across the range wins
const TICK_STEP_HOURS = [1, 2, 3, 6, 12, 24, 48, 168];
const MAX_TICKS = 7;

const pad2 = (n: number) => String(n).padStart(2, '0');

/**
 * Hour-aligned axis ticks (browser time) between min and max, so ticks sit at even local times
 * such as 06:00 or midnight. Returns the step too, which decides the label format.
 */
export function buildTrendTicks(min: number, max: number): { ticks: number[]; stepHours: number } {
  if (!(max > min)) return { ticks: [min], stepHours: 1 };
  const spanHours = (max - min) / HOUR_MS;
  const stepHours =
    TICK_STEP_HOURS.find((step) => spanHours / step <= MAX_TICKS) ?? TICK_STEP_HOURS[TICK_STEP_HOURS.length - 1];

  const cursor = new Date(min);
  cursor.setMinutes(0, 0, 0);
  if (stepHours >= 24) {
    cursor.setHours(0);
  } else {
    cursor.setHours(cursor.getHours() - (cursor.getHours() % stepHours));
  }
  const ticks: number[] = [];
  // Step with calendar arithmetic rather than fixed milliseconds so ticks stay aligned across DST changes
  while (cursor.getTime() <= max) {
    if (cursor.getTime() >= min) ticks.push(cursor.getTime());
    if (stepHours >= 24) {
      cursor.setDate(cursor.getDate() + stepHours / 24);
    } else {
      cursor.setHours(cursor.getHours() + stepHours);
    }
  }
  return { ticks, stepHours };
}

/** Axis label: 'M/D' for day steps, 'M/D HH:00' for hour steps. */
export function formatTrendTick(ms: number, stepHours: number): string {
  const d = new Date(ms);
  const day = `${d.getMonth() + 1}/${d.getDate()}`;
  return stepHours >= 24 ? day : `${day} ${pad2(d.getHours())}:00`;
}

function formatTrendTooltip(ms: number): string {
  const d = new Date(ms);
  return `${d.getMonth() + 1}/${d.getDate()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

export const StorageTrendChart: React.FC<StorageTrendChartProps> = ({ history }) => {
  if (!history || history.length === 0) {
    return (
      <div className="bg-dark-900 border border-dark-700 rounded-xl p-5 shadow-md">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-4 flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-accent-500" />
          <span>30-Day Storage & Volume Trend</span>
        </h3>
        <div className="h-48 flex items-center justify-center text-slate-500 font-mono text-xs">
          No historical storage metrics recorded yet. Metrics are sampled hourly.
        </div>
      </div>
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
  const { ticks, stepHours } = buildTrendTicks(minTime, maxTime);
  // A single snapshot has no range, so pad the domain to keep the point on the chart
  const domain = maxTime > minTime ? [minTime, maxTime] : [minTime - HOUR_MS / 2, maxTime + HOUR_MS / 2];

  return (
    <div className="bg-dark-900 border border-dark-700 rounded-xl p-5 shadow-md">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-accent-500" />
          <span>30-Day Storage & Volume Trend</span>
        </h3>
        <span className="text-[11px] font-mono text-slate-400">Hourly Snapshots</span>
      </div>

      <div className="h-56 w-full" data-testid="storage-trend-chart">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="dbSizeGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#0ea5e9" stopOpacity={0.4} />
                <stop offset="95%" stopColor="#0ea5e9" stopOpacity={0.0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis
              dataKey="time"
              type="number"
              scale="time"
              domain={domain}
              ticks={ticks}
              tickFormatter={(val: number) => formatTrendTick(val, stepHours)}
              stroke="#64748b"
              tick={{ fontSize: 10 }}
            />
            <YAxis
              stroke="#64748b"
              tick={{ fontSize: 10 }}
              width={70}
              tickFormatter={(val: number) => (val >= 10 ? Math.round(val).toLocaleString() : String(val))}
              unit="MB"
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#0b0f19',
                borderColor: '#334155',
                borderRadius: '8px',
                fontSize: '11px',
                fontFamily: 'monospace',
              }}
              labelFormatter={(val: any) => formatTrendTooltip(Number(val))}
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
              stroke="#0ea5e9"
              strokeWidth={2}
              fillOpacity={1}
              fill="url(#dbSizeGrad)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};
