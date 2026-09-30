import React, { useState } from 'react';
import { Trash2, Clock, Check, RefreshCw, AlertCircle, Info, Lock, Database } from 'lucide-react';
import { triggerManualPrune, triggerVacuum } from '../../api/system.ts';
import { formatBytes } from './StorageCard.tsx';
import { PruneResponse, VacuumResponse } from '../../types.ts';
import { Modal } from '../common/Modal.tsx';

interface RetentionSliderProps {
  retentionDays: number;
  maxRetentionDays?: number;
  retentionOverridden?: boolean;
  onSaveRetention: (days: number) => Promise<void>;
  onPruneCompleted?: () => void;
  onVacuumCompleted?: () => void;
}

export const RetentionSlider: React.FC<RetentionSliderProps> = ({
  retentionDays,
  maxRetentionDays = 30,
  retentionOverridden = false,
  onSaveRetention,
  onPruneCompleted,
  onVacuumCompleted,
}) => {
  const min = 1;
  const max = Math.max(1, maxRetentionDays || 30);
  const clampDays = (d: number) => Math.min(Math.max(d || 14, min), max);

  const [days, setDays] = useState<number>(() => clampDays(retentionDays));
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [saveSuccess, setSaveSuccess] = useState<boolean>(false);

  const [isPruning, setIsPruning] = useState<boolean>(false);
  const [pruneResult, setPruneResult] = useState<PruneResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [isCompacting, setIsCompacting] = useState<boolean>(false);
  const [isVacuumModalOpen, setIsVacuumModalOpen] = useState<boolean>(false);
  const [vacuumResult, setVacuumResult] = useState<VacuumResponse | null>(null);
  const [vacuumErrorMsg, setVacuumErrorMsg] = useState<string | null>(null);

  const handleVacuumConfirm = async () => {
    setIsVacuumModalOpen(false);
    try {
      setIsCompacting(true);
      setVacuumErrorMsg(null);
      setVacuumResult(null);
      const res = await triggerVacuum();
      setVacuumResult(res);
      if (onVacuumCompleted) {
        onVacuumCompleted();
      } else if (onPruneCompleted) {
        onPruneCompleted();
      }
    } catch (err: any) {
      setVacuumErrorMsg(err.message || 'Database compaction failed.');
    } finally {
      setIsCompacting(false);
    }
  };

  const handleSave = async () => {
    try {
      setIsSaving(true);
      setErrorMsg(null);
      await onSaveRetention(days);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2500);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to update retention period.');
    } finally {
      setIsSaving(false);
    }
  };

  const handlePruneNow = async () => {
    if (!window.confirm('Trigger immediate purge of expired logs now?')) return;
    try {
      setIsPruning(true);
      setErrorMsg(null);
      const res = await triggerManualPrune();
      setPruneResult(res);
      if (onPruneCompleted) onPruneCompleted();
    } catch (err: any) {
      setErrorMsg(err.message || 'Manual purge failed.');
    } finally {
      setIsPruning(false);
    }
  };

  // Base presets: [3, 7, 14, 30]
  // Include higher presets up to maxRetentionDays if configured
  const candidatePresets = [3, 7, 14, 30, 60, 90, 180, 365];
  const presets = candidatePresets.filter((p) => p <= max);
  if (!presets.includes(max)) {
    presets.push(max);
  }
  presets.sort((a, b) => a - b);

  // Sync internal state if prop updates
  React.useEffect(() => {
    setDays(clampDays(retentionDays));
  }, [retentionDays]);

  React.useEffect(() => {
    setDays((prev) => clampDays(prev));
  }, [max, min]);

  return (
    <div className="bg-dark-900 border border-dark-700 rounded-xl p-5 shadow-md space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center gap-2">
          <Clock className="w-4 h-4 text-accent-500" />
          <span>Log Retention Policy</span>
        </h3>
        <span className="font-mono text-xs text-accent-400 font-bold">
          {days} Day{days === 1 ? '' : 's'}
        </span>
      </div>

      {errorMsg && (
        <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Override Notice & Badge */}
      {retentionOverridden && (
        <div className="space-y-1.5 pt-0.5">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-amber-500/10 border border-amber-500/20 text-[11px] font-mono text-amber-300">
            <Lock className="w-3.5 h-3.5 text-amber-400 shrink-0" />
            <span>Locked by MAX_RETENTION_DAYS environment variable override ({max} days)</span>
          </div>
          {max > 30 && (
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Extended retention (&gt;30 days) requires additional disk storage and may increase search times over large log volumes.
            </p>
          )}
        </div>
      )}

      {/* Preset Quick-Select Buttons */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-slate-400 font-medium mr-1">Presets:</span>
        {presets.map((preset) => (
          <button
            key={preset}
            type="button"
            disabled={retentionOverridden}
            onClick={() => !retentionOverridden && setDays(preset)}
            className={`px-2.5 py-0.5 rounded text-[11px] font-mono transition ${
              retentionOverridden
                ? 'opacity-40 cursor-not-allowed bg-dark-950 text-slate-500 border border-dark-800'
                : days === preset
                ? 'bg-accent-600 text-white font-semibold shadow-xs cursor-pointer'
                : 'bg-dark-950 text-slate-400 hover:text-slate-200 hover:bg-dark-800 border border-dark-700 cursor-pointer'
            }`}
          >
            {preset} Day{preset === 1 ? '' : 's'}
          </button>
        ))}
      </div>

      {/* Slider Control */}
      <div className="relative pt-1 pb-2">
        <input
          type="range"
          min={min}
          max={max}
          value={days}
          disabled={retentionOverridden}
          onChange={(e) => !retentionOverridden && setDays(parseInt(e.target.value, 10))}
          className={`w-full accent-accent-500 bg-dark-950 h-2 rounded-lg ${
            retentionOverridden ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'
          }`}
        />
        <div className="relative h-6 text-[10px] font-mono text-slate-500 mt-1 select-none">
          {presets.map((val) => {
            const leftPercent = max === min ? 0 : ((val - min) / (max - min)) * 100;
            return (
              <div
                key={val}
                style={{ left: `${leftPercent}%` }}
                onClick={() => !retentionOverridden && setDays(val)}
                onKeyDown={(e) => {
                  if (!retentionOverridden && (e.key === 'Enter' || e.key === ' ')) {
                    e.preventDefault();
                    setDays(val);
                  }
                }}
                role="button"
                tabIndex={retentionOverridden ? -1 : 0}
                aria-label={`Set retention to ${val} day${val === 1 ? '' : 's'}`}
                className={`absolute -translate-x-1/2 flex flex-col items-center group transition focus:outline-hidden ${
                  retentionOverridden
                    ? 'cursor-not-allowed opacity-40'
                    : 'cursor-pointer hover:text-accent-400 focus:text-accent-400'
                }`}
                title={`Set retention to ${val} day${val === 1 ? '' : 's'}`}
              >
                <div
                  className={`w-0.5 h-1.5 mb-0.5 transition ${
                    days === val ? 'bg-accent-400' : 'bg-slate-600 group-hover:bg-slate-400'
                  }`}
                />
                <span
                  className={`transition whitespace-nowrap ${
                    days === val ? 'text-accent-400 font-bold' : 'group-hover:text-slate-300'
                  }`}
                >
                  {`${val}d`}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-dark-800">
        <button
          onClick={handleSave}
          disabled={isSaving || days === retentionDays || retentionOverridden}
          className={`font-medium px-4 py-1.5 rounded-lg text-xs flex items-center gap-1.5 transition ${
            isSaving || days === retentionDays || retentionOverridden
              ? 'opacity-40 cursor-not-allowed bg-dark-800 text-slate-500 border border-dark-700'
              : 'bg-accent-600 hover:bg-accent-500 text-white cursor-pointer shadow-md'
          }`}
        >
          {saveSuccess ? (
            <Check className="w-3.5 h-3.5 text-emerald-400" />
          ) : isSaving ? (
            <RefreshCw className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Clock className="w-3.5 h-3.5" />
          )}
          <span>{saveSuccess ? 'Saved!' : 'Save Retention Policy'}</span>
        </button>

        <div className="flex flex-wrap items-center gap-1.5">
          <button
            onClick={() => setIsVacuumModalOpen(true)}
            disabled={isCompacting || isPruning}
            title="Repack SQLite database file and return freelist space to host filesystem."
            className="bg-accent-950/80 hover:bg-accent-900 text-accent-300 border border-accent-800 font-medium px-4 py-1.5 rounded-lg text-xs flex items-center gap-1.5 transition shadow-xs cursor-pointer disabled:cursor-not-allowed"
          >
            {isCompacting ? (
              <>
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                <span>Compacting database... Ingestion writes temporarily paused</span>
              </>
            ) : (
              <>
                <Database className="w-3.5 h-3.5" />
                <span>Compact Database</span>
              </>
            )}
          </button>

          <button
            onClick={handlePruneNow}
            disabled={isPruning || isCompacting}
            title="Immediately purge logs older than the configured retention policy, compact the search index, and checkpoint the SQLite WAL."
            className="bg-red-950/80 hover:bg-red-900 text-red-300 border border-red-800 font-medium px-4 py-1.5 rounded-lg text-xs flex items-center gap-1.5 transition shadow-xs cursor-pointer disabled:cursor-not-allowed"
          >
            {isPruning ? (
              <>
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                <span>Purging & Compacting Index...</span>
              </>
            ) : (
              <>
                <Trash2 className="w-3.5 h-3.5" />
                <span>Purge Expired Logs Now</span>
              </>
            )}
          </button>
          <div
            className="text-slate-500 hover:text-slate-300 transition cursor-help p-0.5"
            title="Pruning runs automatically once every 24 hours. SQLite automatically reuses free database pages for incoming logs without requiring an exclusive offline VACUUM. Click 'Purge Expired Logs Now' if you recently lowered your retention days and want to immediately purge older logs, compact the search index, and checkpoint the WAL. Click 'Compact Database' to repack the database file and return freelist space to the host filesystem."
          >
            <Info className="w-4 h-4" />
          </div>
        </div>
      </div>

      {/* Explanatory Caption */}
      <p className="text-[11px] text-slate-500 leading-relaxed">
        Old logs are automatically cleaned up daily. Purging deletes them immediately and frees space for new logs.
      </p>

      {/* Active Vacuum Progress Banner */}
      {isCompacting && (
        <div className="p-3 bg-dark-950 border border-accent-800/60 rounded-lg text-xs text-accent-300 flex items-center gap-2 animate-pulse font-mono">
          <RefreshCw className="w-4 h-4 animate-spin text-accent-400 shrink-0" />
          <span>Compacting database... Ingestion writes temporarily paused</span>
        </div>
      )}

      {/* Vacuum Error Banner */}
      {vacuumErrorMsg && (
        <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{vacuumErrorMsg}</span>
        </div>
      )}

      {/* Vacuum Result Banner */}
      {vacuumResult && (
        <div className="p-3 bg-dark-950 border border-accent-900/60 rounded-lg text-xs font-mono text-slate-300 animate-in fade-in">
          <span className="text-accent-400 font-semibold block mb-1">Database Compaction Completed Successfully:</span>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px]">
            <div>Previous Footprint: <span className="text-slate-100">{formatBytes(vacuumResult.previous_size_bytes)}</span></div>
            <div>New Footprint: <span className="text-slate-100">{formatBytes(vacuumResult.new_size_bytes)}</span></div>
            <div>Reclaimed Space: <span className="text-emerald-400 font-semibold">{formatBytes(vacuumResult.reclaimed_bytes)}</span></div>
          </div>
        </div>
      )}

      {/* Prune Result Banner */}
      {pruneResult && (
        <div className="p-3 bg-dark-950 border border-emerald-900/60 rounded-lg text-xs font-mono text-slate-300 animate-in fade-in">
          <span className="text-emerald-400 font-semibold block mb-1">Prune Completed Successfully:</span>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div>Deleted Records: <span className="text-slate-100">{pruneResult.deleted_logs}</span></div>
            <div>Deleted Metrics: <span className="text-slate-100">{pruneResult.deleted_metrics}</span></div>
            <div>Current DB Footprint: <span className="text-slate-100">{formatBytes(pruneResult.metrics.db_size_bytes)}</span></div>
          </div>
        </div>
      )}

      {/* Database Compaction Modal */}
      <Modal
        isOpen={isVacuumModalOpen}
        onClose={() => !isCompacting && setIsVacuumModalOpen(false)}
        title="Compact Database"
        maxWidth="max-w-md"
      >
        <div className="space-y-4 text-xs text-slate-300">
          <p className="leading-relaxed">
            The database will be repacked to return unused space to the host filesystem.
          </p>
          <div className="space-y-2 bg-dark-950 border border-dark-800 rounded-lg p-3 text-[11px] text-slate-400">
            <div className="flex items-start gap-2">
              <span className="text-accent-400 font-bold">•</span>
              <span>SQLite database writes will pause briefly during compaction (incoming logs will buffer in memory).</span>
            </div>
            <div className="flex items-start gap-2">
              <span className="text-accent-400 font-bold">•</span>
              <span>Requires temporary free disk space equal to the current database size.</span>
            </div>
          </div>
          <div className="flex items-center justify-end gap-2 pt-2 border-t border-dark-800">
            <button
              type="button"
              onClick={() => setIsVacuumModalOpen(false)}
              disabled={isCompacting}
              className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded-lg transition cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleVacuumConfirm}
              disabled={isCompacting}
              className="px-4 py-1.5 text-xs bg-accent-600 hover:bg-accent-500 text-white font-medium rounded-lg shadow-sm transition flex items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed"
            >
              <Database className="w-3.5 h-3.5" />
              <span>Confirm & Compact</span>
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
};

