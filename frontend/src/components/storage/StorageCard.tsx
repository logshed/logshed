import React from 'react';
import { Database, HardDrive, FileText } from 'lucide-react';
import { StorageMetricsResponse } from '../../types.ts';

interface StorageCardProps {
  metrics: StorageMetricsResponse | null;
  totalLogs?: number;
}
import { formatBytes } from '../../utils/formatters.ts';

export { formatBytes };

export const StorageCard: React.FC<StorageCardProps> = ({ metrics }) => {
  if (!metrics) {
    return (
      <div className="bg-dark-900 border border-dark-700 rounded-xl p-4 animate-pulse">
        <div className="h-4 bg-dark-800 rounded w-1/3 mb-4"></div>
        <div className="h-8 bg-dark-800 rounded w-full"></div>
      </div>
    );
  }

  const diskUsedBytes = metrics.disk_total_bytes - metrics.disk_free_bytes;
  const usedPercent = metrics.disk_total_bytes > 0
    ? Math.min(100, Math.max(0, Math.round((diskUsedBytes / metrics.disk_total_bytes) * 100)))
    : 0;

  // Real-time current log count from metrics.total_logs_count, or fallback to latest history snapshot
  const totalLogs = typeof metrics.total_logs_count === 'number'
    ? metrics.total_logs_count
    : (metrics.history && metrics.history.length > 0
      ? metrics.history[metrics.history.length - 1].total_logs_count
      : 0);

  return (
    <div className="bg-dark-900 border border-dark-700 rounded-xl p-5 shadow-md">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
          <Database className="w-4 h-4 text-accent-500" />
          <span>Current Storage Footprint</span>
        </h3>
        <span className="font-mono text-[11px] text-slate-400">Mount: /data</span>
      </div>

      {/* Metrics Row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-5">
        <div className="bg-dark-950 p-3 rounded-lg border border-dark-800">
          <span className="text-[11px] text-slate-400 font-medium block mb-1 flex items-center gap-1.5">
            <Database className="w-3.5 h-3.5 text-accent-400" />
            Database Size
          </span>
          <span className="text-lg font-bold font-mono text-slate-100" data-testid="db-size-display">
            {formatBytes(metrics.db_size_bytes)}
          </span>
          <span className="text-[10px] text-slate-500 block mt-0.5 font-mono">logs.db + WAL + SHM</span>
        </div>

        <div className="bg-dark-950 p-3 rounded-lg border border-dark-800">
          <span className="text-[11px] text-slate-400 font-medium block mb-1 flex items-center gap-1.5">
            <HardDrive className="w-3.5 h-3.5 text-emerald-400" />
            Disk Free Space
          </span>
          <span className="text-lg font-bold font-mono text-slate-100" data-testid="disk-free-display">
            {formatBytes(metrics.disk_free_bytes)}
          </span>
          <span className="text-[10px] text-slate-500 block mt-0.5 font-mono">
            of {formatBytes(metrics.disk_total_bytes)} total
          </span>
        </div>

        <div className="bg-dark-950 p-3 rounded-lg border border-dark-800">
          <span className="text-[11px] text-slate-400 font-medium block mb-1 flex items-center gap-1.5">
            <FileText className="w-3.5 h-3.5 text-indigo-400" />
            Total Logs Stored
          </span>
          <span className="text-lg font-bold font-mono text-slate-100">
            {totalLogs.toLocaleString()}
          </span>
          <span className="text-[10px] text-slate-500 block mt-0.5 font-mono">Indexed in FTS5</span>
        </div>
      </div>

      {/* Disk Usage Progress Bar */}
      <div>
        <div className="flex justify-between text-xs font-mono text-slate-400 mb-1.5">
          <span>Mount Capacity Used ({usedPercent}%)</span>
          <span>{formatBytes(diskUsedBytes)} / {formatBytes(metrics.disk_total_bytes)}</span>
        </div>
        <div className="w-full h-2.5 bg-dark-950 rounded-full overflow-hidden border border-dark-700">
          <div
            data-testid="disk-usage-bar"
            style={{ width: `${usedPercent}%` }}
            className={`h-full transition-all duration-500 ${
              usedPercent > 90
                ? 'bg-red-500'
                : usedPercent > 75
                ? 'bg-amber-500'
                : 'bg-accent-500'
            }`}
          />
        </div>
      </div>
    </div>
  );
};
