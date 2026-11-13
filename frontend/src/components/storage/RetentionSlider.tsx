import React, { useState } from 'react';
import { Trash2, Clock, Check, RefreshCw, AlertCircle, Info, Lock, Database } from 'lucide-react';
import { fetchPrunePreview, triggerManualPrune, triggerVacuum } from '../../api/system.ts';
import { formatBytes } from '../../utils/formatters.ts';
import { PrunePreview, PruneResponse, VacuumResponse } from '../../types.ts';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';
import { PrunePreviewModal, PrunePreviewMode } from './PrunePreviewModal.tsx';
import { useFlash } from '../../utils/hooks.ts';

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
  const [saveSuccess, flashSaveSuccess] = useFlash<true>(2500);

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

  const [previewMode, setPreviewMode] = useState<PrunePreviewMode | null>(null);
  const [preview, setPreview] = useState<PrunePreview | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState<boolean>(false);

  const closePreview = () => {
    setPreviewMode(null);
    setPreview(null);
  };

  const saveRetention = async () => {
    await onSaveRetention(days);
    flashSaveSuccess(true);
  };

  const runPrune = async () => {
    setIsPruning(true);
    try {
      const res = await triggerManualPrune();
      setPruneResult(res);
      if (onPruneCompleted) onPruneCompleted();
    } finally {
      setIsPruning(false);
    }
  };

  const handleSave = async () => {
    try {
      setIsSaving(true);
      setErrorMsg(null);
      if (days < retentionDays) {
        // Lowering retention can delete data: show what would go before saving
        let result: PrunePreview;
        try {
          result = await fetchPrunePreview(days);
        } catch (err: any) {
          setErrorMsg(err.message || 'Could not check what a lower retention period would delete. Nothing was saved.');
          return;
        }
        // Old metric samples are removed at every cleanup regardless of retention, so
        // they alone do not justify interrupting a save.
        if (result.total - result.metrics > 0) {
          setPreview(result);
          setPreviewMode('save');
          return;
        }
      }
      await saveRetention();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to update retention period.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveAndDeleteLater = async () => {
    closePreview();
    try {
      setIsSaving(true);
      setErrorMsg(null);
      await saveRetention();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to update retention period.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSaveAndDeleteNow = async () => {
    try {
      setIsSaving(true);
      setErrorMsg(null);
      try {
        await saveRetention();
      } catch (err: any) {
        setErrorMsg(err.message || 'Failed to update retention period.');
        return;
      }
      try {
        await runPrune();
      } catch (err: any) {
        // The new retention is already saved: only the immediate purge failed
        setErrorMsg(
          err?.status === 409
            ? 'Retention saved. Purge is busy, try again shortly.'
            : `Retention saved, but the purge failed: ${err?.message || 'unknown error'}`
        );
      }
    } finally {
      setIsSaving(false);
      closePreview();
    }
  };

  const handleCancelPreview = () => {
    if (previewMode === 'save') {
      // Nothing was saved, so return the slider to the saved retention
      setDays(clampDays(retentionDays));
    }
    closePreview();
  };

  const handlePruneNow = async () => {
    try {
      setIsLoadingPreview(true);
      setErrorMsg(null);
      const result = await fetchPrunePreview();
      setPreview(result);
      setPreviewMode('purge');
    } catch (err: any) {
      setErrorMsg(err.message || 'Could not check what would be purged.');
    } finally {
      setIsLoadingPreview(false);
    }
  };

  const handleConfirmPurge = async () => {
    try {
      setErrorMsg(null);
      await runPrune();
    } catch (err: any) {
      setErrorMsg(err.message || 'Manual purge failed.');
    } finally {
      closePreview();
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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- clampDays is a new function each render; listing it would reset the slider while it is dragged
  }, [retentionDays]);

  React.useEffect(() => {
    setDays((prev) => clampDays(prev));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- clampDays reads only min and max, listed here
  }, [max, min]);

  return (
    <div className="bg-surface border border-line rounded-xl p-5 shadow-md space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-fg-soft uppercase tracking-wider flex items-center gap-2">
          <Clock className="w-4 h-4 text-accent-500" />
          <span>Log Retention Policy</span>
        </h3>
        <span className="font-mono text-xs text-accent-400 font-bold">
          {days} Day{days === 1 ? '' : 's'}
        </span>
      </div>

      {errorMsg && (
        <div className="p-3 bg-danger-950/60 border border-danger-800 rounded-lg flex items-start gap-2 text-xs text-danger-300">
          <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Override Notice & Badge */}
      {retentionOverridden && (
        <div className="space-y-1.5 pt-0.5">
          <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-warning-500/10 border border-warning-500/20 text-[11px] font-mono text-warning-300">
            <Lock className="w-3.5 h-3.5 text-warning-400 shrink-0" />
            <span>Locked by MAX_RETENTION_DAYS environment variable override ({max} days)</span>
          </div>
          {max > 30 && (
            <p className="text-[11px] text-fg-muted leading-relaxed">
              Extended retention (&gt;30 days) requires additional disk storage and may increase search times over large log volumes.
            </p>
          )}
        </div>
      )}

      {/* Preset Quick-Select Buttons */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-fg-muted font-medium mr-1">Presets:</span>
        {presets.map((preset) => (
          <button
            key={preset}
            type="button"
            disabled={retentionOverridden}
            onClick={() => !retentionOverridden && setDays(preset)}
            className={`px-2.5 py-0.5 rounded text-[11px] font-mono transition ${
              retentionOverridden
                ? 'opacity-40 cursor-not-allowed bg-canvas text-fg-subtle border border-line-subtle'
                : days === preset
                ? 'bg-accent-700 text-on-fill font-semibold shadow-xs cursor-pointer'
                : 'bg-canvas text-fg-muted hover:text-fg hover:bg-raised border border-line cursor-pointer'
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
          aria-label="Retention period in days"
          aria-valuetext={`${days} day${days === 1 ? '' : 's'}`}
          min={min}
          max={max}
          value={days}
          disabled={retentionOverridden}
          onChange={(e) => !retentionOverridden && setDays(parseInt(e.target.value, 10))}
          className={`w-full accent-accent-500 bg-canvas h-2 rounded-lg ${
            retentionOverridden ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'
          }`}
        />
        <div className="relative h-6 text-[10px] font-mono text-fg-muted mt-1 select-none">
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
                className={`absolute -translate-x-1/2 flex flex-col items-center group transition ${
                  retentionOverridden
                    ? 'cursor-not-allowed opacity-40'
                    : 'cursor-pointer hover:text-accent-400 focus:text-accent-400'
                }`}
                title={`Set retention to ${val} day${val === 1 ? '' : 's'}`}
              >
                <div
                  className={`w-0.5 h-1.5 mb-0.5 transition ${
                    days === val ? 'bg-accent-400' : 'bg-neutral-600 group-hover:bg-neutral-400'
                  }`}
                />
                <span
                  className={`transition whitespace-nowrap ${
                    days === val ? 'text-accent-400 font-bold' : 'group-hover:text-fg-soft'
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
      <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-line-subtle">
        <button
          onClick={handleSave}
          disabled={isSaving || days === retentionDays || retentionOverridden}
          className={`font-medium px-4 py-1.5 rounded-lg text-xs flex items-center gap-1.5 transition ${
            isSaving || days === retentionDays || retentionOverridden
              ? 'opacity-40 cursor-not-allowed bg-raised text-fg-subtle border border-line'
              : 'bg-accent-700 hover:bg-accent-600 text-on-fill cursor-pointer shadow-md'
          }`}
        >
          {saveSuccess ? (
            <Check className="w-3.5 h-3.5 text-success-400" />
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
            disabled={isPruning || isCompacting || isLoadingPreview}
            title="Show what is older than the configured retention policy, then purge those logs and history entries, compact the search index, and checkpoint the SQLite WAL."
            className="bg-danger-950/80 hover:bg-danger-900 text-danger-300 border border-danger-800 font-medium px-4 py-1.5 rounded-lg text-xs flex items-center gap-1.5 transition shadow-xs cursor-pointer disabled:cursor-not-allowed"
          >
            {isPruning ? (
              <>
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                <span>Purging & Compacting Index...</span>
              </>
            ) : (
              <>
                <Trash2 className="w-3.5 h-3.5" />
                <span>Purge Expired Logs & History Now</span>
              </>
            )}
          </button>
          <div
            className="text-fg-subtle hover:text-fg-soft transition cursor-help p-0.5"
            title="Pruning runs automatically once every 24 hours. SQLite automatically reuses free database pages for incoming logs without requiring an exclusive offline VACUUM. Lowering retention shows what would be deleted and lets you delete it now or at the next daily cleanup. Click 'Purge Expired Logs & History Now' to review and immediately purge older logs and history entries, compact the search index, and checkpoint the WAL. Click 'Compact Database' to repack the database file and return freelist space to the host filesystem."
          >
            <Info className="w-4 h-4" />
          </div>
        </div>
      </div>

      {/* Explanatory Caption */}
      <p className="text-[11px] text-fg-muted leading-relaxed">
        Old logs and history are automatically cleaned up daily. Purging deletes them immediately and frees space for new logs.
      </p>

      {/* Active Vacuum Progress Banner */}
      {isCompacting && (
        <div className="p-3 bg-canvas border border-accent-800/60 rounded-lg text-xs text-accent-300 flex items-center gap-2 animate-pulse font-mono">
          <RefreshCw className="w-4 h-4 animate-spin text-accent-400 shrink-0" />
          <span>Compacting database... Ingestion writes temporarily paused</span>
        </div>
      )}

      {/* Vacuum Error Banner */}
      {vacuumErrorMsg && (
        <div className="p-3 bg-danger-950/60 border border-danger-800 rounded-lg flex items-start gap-2 text-xs text-danger-300">
          <AlertCircle className="w-4 h-4 text-danger-400 shrink-0 mt-0.5" />
          <span>{vacuumErrorMsg}</span>
        </div>
      )}

      {/* Vacuum Result Banner */}
      {vacuumResult && (
        <div className="p-3 bg-canvas border border-accent-900/60 rounded-lg text-xs font-mono text-fg-soft animate-in fade-in">
          <span className="text-accent-400 font-semibold block mb-1">Database Compaction Completed Successfully:</span>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px]">
            <div>Previous Footprint: <span className="text-fg-strong">{formatBytes(vacuumResult.previous_size_bytes)}</span></div>
            <div>New Footprint: <span className="text-fg-strong">{formatBytes(vacuumResult.new_size_bytes)}</span></div>
            <div>Reclaimed Space: <span className="text-success-400 font-semibold">{formatBytes(vacuumResult.reclaimed_bytes)}</span></div>
          </div>
        </div>
      )}

      {/* Prune Result Banner */}
      {pruneResult && (
        <div className="p-3 bg-canvas border border-success-900/60 rounded-lg text-xs font-mono text-fg-soft animate-in fade-in">
          <span className="text-success-400 font-semibold block mb-1">Prune Completed Successfully:</span>
          <div className="grid grid-cols-2 gap-2 text-[11px]">
            <div>Deleted Logs: <span className="text-fg-strong">{pruneResult.deleted_logs}</span></div>
            <div>Deleted History Entries: <span className="text-fg-strong">{pruneResult.deleted_history}</span></div>
            <div>Deleted AI Audit Records: <span className="text-fg-strong">{pruneResult.deleted_ai_audit}</span></div>
            <div>Deleted Metrics: <span className="text-fg-strong">{pruneResult.deleted_metrics}</span></div>
            <div>Current DB Footprint: <span className="text-fg-strong">{formatBytes(pruneResult.metrics.db_size_bytes)}</span></div>
          </div>
        </div>
      )}

      {/* Retention Preview Modal */}
      <PrunePreviewModal
        isOpen={previewMode !== null}
        mode={previewMode ?? 'purge'}
        preview={preview}
        days={previewMode === 'save' ? days : (preview?.retention_days ?? retentionDays)}
        busy={isSaving || isPruning}
        onDeleteNow={previewMode === 'save' ? handleSaveAndDeleteNow : handleConfirmPurge}
        onDeleteLater={handleSaveAndDeleteLater}
        onCancel={handleCancelPreview}
      />

      {/* Database Compaction Modal */}
      <ConfirmDialog
        isOpen={isVacuumModalOpen}
        title="Compact Database"
        onCancel={() => setIsVacuumModalOpen(false)}
        onClose={() => !isCompacting && setIsVacuumModalOpen(false)}
        onConfirm={handleVacuumConfirm}
        cancelDisabled={isCompacting}
        confirmDisabled={isCompacting}
        confirmIcon={<Database className="w-3.5 h-3.5" />}
        confirmLabel="Confirm & Compact"
        bodyClassName="space-y-4 text-xs text-fg-soft"
        footerClassName="flex items-center justify-end gap-2 pt-2 border-t border-line-subtle"
        cancelClassName="px-3 py-1.5 text-xs text-fg-muted hover:text-fg hover:bg-raised rounded-lg transition cursor-pointer"
        confirmClassName="px-4 py-1.5 text-xs bg-accent-700 hover:bg-accent-600 text-on-fill font-medium rounded-lg shadow-xs transition flex items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed"
      >
        <p className="leading-relaxed">
          The database will be repacked to return unused space to the host filesystem.
        </p>
        <div className="space-y-2 bg-canvas border border-line-subtle rounded-lg p-3 text-[11px] text-fg-muted">
          <div className="flex items-start gap-2">
            <span className="text-accent-400 font-bold">•</span>
            <span>SQLite database writes will pause briefly during compaction (incoming logs will buffer in memory).</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="text-accent-400 font-bold">•</span>
            <span>Needs free disk space of about twice the current database size while it runs.</span>
          </div>
        </div>
      </ConfirmDialog>
    </div>
  );
};

