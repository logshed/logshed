import React from 'react';
import { Database, HardDrive, FileText } from 'lucide-react';
import { StorageMetricsResponse } from '../../types.ts';
import { Card, CardHeader } from '../common/Card.tsx';

interface StorageCardProps {
  metrics: StorageMetricsResponse | null;
  totalLogs?: number;
}
import { formatBytes } from '../../utils/formatters.ts';

/** What the Database Size figure is made of: logs.db and the day files. */
export function dbSplitLabel(metrics: StorageMetricsResponse): string {
  const dayFiles = (metrics.log_files ?? []).filter((f) => f.state !== 'legacy').length;
  if (metrics.main_db_size_bytes === undefined || dayFiles === 0) return 'logs.db + WAL + SHM';
  return `logs.db ${formatBytes(metrics.main_db_size_bytes)} + ${dayFiles} day ${dayFiles === 1 ? 'file' : 'files'} ${formatBytes(
    metrics.day_files_size_bytes ?? 0
  )}`;
}

export const StorageCard: React.FC<StorageCardProps> = ({ metrics }) => {
  if (!metrics) {
    return (
      <div className="bg-surface border border-line rounded-xl p-4 animate-pulse">
        <div className="h-4 bg-raised rounded-sm w-1/3 mb-4"></div>
        <div className="h-8 bg-raised rounded-sm w-full"></div>
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
    <Card>
      <CardHeader
        icon={<Database />}
        title="Current Storage Footprint"
        actions={<span className="font-mono text-[11px] text-fg-muted">Mount: /data</span>}
      />

      {/* Metrics Row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-canvas p-3 rounded-lg border border-line-subtle">
          <span className="text-[11px] text-fg-muted font-medium block mb-1 flex items-center gap-1.5">
            <Database className="w-3.5 h-3.5 text-accent-400" />
            Database Size
          </span>
          <span className="text-lg font-bold font-mono text-fg-strong" data-testid="db-size-display">
            {formatBytes(metrics.db_size_bytes)}
          </span>
          <span className="text-[11px] text-fg-muted block mt-0.5 font-mono" data-testid="db-split-display">
            {dbSplitLabel(metrics)}
          </span>
          <span className="text-[11px] text-fg-muted block mt-0.5 font-mono" data-testid="backups-size-display">
            Backups: {formatBytes(metrics.backups_size_bytes ?? 0)}
          </span>
        </div>

        <div className="bg-canvas p-3 rounded-lg border border-line-subtle">
          <span className="text-[11px] text-fg-muted font-medium block mb-1 flex items-center gap-1.5">
            <HardDrive className="w-3.5 h-3.5 text-success-400" />
            Disk Free Space
          </span>
          <span className="text-lg font-bold font-mono text-fg-strong" data-testid="disk-free-display">
            {formatBytes(metrics.disk_free_bytes)}
          </span>
          <span className="text-[11px] text-fg-muted block mt-0.5 font-mono">
            of {formatBytes(metrics.disk_total_bytes)} total
          </span>
        </div>

        <div className="bg-canvas p-3 rounded-lg border border-line-subtle">
          <span className="text-[11px] text-fg-muted font-medium block mb-1 flex items-center gap-1.5">
            <FileText className="w-3.5 h-3.5 text-marker-400" />
            Total Logs Stored
          </span>
          <span className="text-lg font-bold font-mono text-fg-strong">
            {totalLogs.toLocaleString()}
          </span>
          <span className="text-[11px] text-fg-muted block mt-0.5 font-mono">Indexed in FTS5</span>
        </div>
      </div>

      {/* Disk Usage Progress Bar */}
      <div>
        <div className="flex justify-between text-xs font-mono text-fg-muted mb-1.5">
          <span>Mount Capacity Used ({usedPercent}%)</span>
          <span>{formatBytes(diskUsedBytes)} / {formatBytes(metrics.disk_total_bytes)}</span>
        </div>
        <div className="w-full h-2.5 bg-canvas rounded-full overflow-hidden border border-line">
          <div
            data-testid="disk-usage-bar"
            style={{ width: `${usedPercent}%` }}
            className={`h-full transition-all duration-500 ${
              usedPercent > 90
                ? 'bg-danger-500'
                : usedPercent > 75
                ? 'bg-warning-500'
                : 'bg-accent-500'
            }`}
          />
        </div>
      </div>
    </Card>
  );
};
