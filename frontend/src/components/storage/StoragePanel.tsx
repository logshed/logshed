import React, { useEffect, useState } from 'react';
import {
  Database,
  AlertCircle,
  RefreshCw,
} from 'lucide-react';
import { StorageMetricsResponse } from '../../types.ts';
import { fetchSettings, updateSettings } from '../../api/settings.ts';
import { fetchStorageMetrics } from '../../api/system.ts';
import { StorageCard } from './StorageCard.tsx';
import { RetentionSlider } from './RetentionSlider.tsx';
import { StorageTrendChart } from './StorageTrendChart.tsx';

export const StoragePanel: React.FC = () => {
  const [storageMetrics, setStorageMetrics] = useState<StorageMetricsResponse | null>(null);
  const [retentionDays, setRetentionDays] = useState<number>(14);
  const [maxRetentionDays, setMaxRetentionDays] = useState<number | undefined>(undefined);
  const [retentionOverridden, setRetentionOverridden] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const loadAllData = async () => {
    try {
      setIsLoading(true);
      setErrorMsg(null);

      const [settRes, storRes] = await Promise.all([
        fetchSettings(),
        fetchStorageMetrics(),
      ]);

      setRetentionDays(settRes.retention_days);
      setMaxRetentionDays(settRes.max_retention_days);
      setRetentionOverridden(Boolean(settRes.retention_overridden));
      setStorageMetrics(storRes);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to load storage and retention data.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadAllData();
  }, []);

  const handleSaveRetention = async (newDays: number) => {
    await updateSettings({ retention_days: newDays });
    setRetentionDays(newDays);
  };

  if (isLoading && !storageMetrics) {
    return (
      <div className="p-8 text-center text-slate-500 font-mono text-xs flex items-center justify-center gap-2">
        <RefreshCw className="w-4 h-4 animate-spin text-accent-500" />
        <span>Loading storage metrics and retention configuration...</span>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto p-3 sm:p-6 space-y-6 sm:space-y-8">
      {/* Header */}
      <div>
        <h2 className="text-base font-bold text-slate-100 flex items-center gap-2">
          <Database className="w-5 h-5 text-accent-500" />
          <span>Storage & Retention</span>
        </h2>
        <p className="text-xs text-slate-400 mt-0.5">
          Monitor SQLite database growth and configure log retention policies.
        </p>
      </div>

      {errorMsg && (
        <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Storage & Retention Section */}
      <section className="space-y-4">
        <StorageCard metrics={storageMetrics} />
        {retentionDays !== undefined && (
          <RetentionSlider
            retentionDays={retentionDays}
            maxRetentionDays={maxRetentionDays}
            retentionOverridden={retentionOverridden}
            onSaveRetention={handleSaveRetention}
            onPruneCompleted={loadAllData}
          />
        )}

        <StorageTrendChart history={storageMetrics?.history || []} />
      </section>
    </div>
  );
};
