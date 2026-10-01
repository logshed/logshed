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
import { formatBytes } from '../../utils/formatters.ts';

interface StorageTrendChartProps {
  history: StorageMetricsSnapshot[];
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

  // Format data for Recharts
  const chartData = history.map((item) => {
    const d = new Date(item.recorded_at);
    const dateLabel = `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:00`;
    return {
      date: dateLabel,
      dbMb: parseFloat((item.db_size_bytes / (1024 * 1024)).toFixed(2)),
      dbBytes: item.db_size_bytes,
      logsCount: item.total_logs_count,
    };
  });

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
            <XAxis dataKey="date" stroke="#64748b" tick={{ fontSize: 10 }} />
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
