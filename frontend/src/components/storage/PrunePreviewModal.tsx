import React from 'react';
import { Trash2, Clock, RefreshCw } from 'lucide-react';
import { Modal } from '../common/Modal.tsx';
import { PrunePreview } from '../../types.ts';

export type PrunePreviewMode = 'save' | 'purge';

interface PrunePreviewModalProps {
  isOpen: boolean;
  mode: PrunePreviewMode;
  preview: PrunePreview | null;
  /** Retention in days that the preview was calculated for. */
  days: number;
  busy: boolean;
  onDeleteNow: () => void;
  onDeleteLater: () => void;
  onCancel: () => void;
}

/** Describes when the next daily cleanup runs, in plain terms. */
export const describeNextCleanup = (nextCleanupAt?: string | null, now: number = Date.now()): string => {
  if (!nextCleanupAt) {
    return 'at the next startup or within 24 hours';
  }
  const diffMs = new Date(nextCleanupAt).getTime() - now;
  if (!Number.isFinite(diffMs)) {
    return 'at the next startup or within 24 hours';
  }
  if (diffMs <= 60 * 1000) {
    return 'within the next minute';
  }
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 60) {
    return `in about ${minutes} minute${minutes === 1 ? '' : 's'}`;
  }
  const hours = Math.round(minutes / 60);
  return `in about ${hours} hour${hours === 1 ? '' : 's'}`;
};

const formatCount = (n: number) => n.toLocaleString();

export const PrunePreviewModal: React.FC<PrunePreviewModalProps> = ({
  isOpen,
  mode,
  preview,
  days,
  busy,
  onDeleteNow,
  onDeleteLater,
  onCancel,
}) => {
  if (!preview) return null;

  const rows: { label: string; count: number }[] = [
    { label: 'Logs', count: preview.logs },
    { label: 'Alert history entries', count: preview.history_alerts },
    { label: 'On-demand analyses', count: preview.history_on_demand },
    { label: 'Daily digests', count: preview.history_digests },
    { label: 'Maintenance windows', count: preview.history_maintenance },
    { label: 'AI audit records', count: preview.ai_audit },
    { label: 'Old storage metric samples', count: preview.metrics },
  ].filter((r) => r.count > 0);

  const nothingToDelete = preview.total === 0;
  const cutoffText = new Date(preview.cutoff).toLocaleString();
  const title = mode === 'save' ? 'Lower Retention Period' : 'Purge Expired Logs and History';

  return (
    <Modal
      isOpen={isOpen}
      onClose={() => !busy && onCancel()}
      title={title}
      maxWidth="max-w-md"
    >
      <div className="space-y-4 text-xs text-slate-300">
        {nothingToDelete ? (
          <p className="leading-relaxed">
            Nothing is older than the {days}-day retention period, so there is nothing to purge right now.
          </p>
        ) : (
          <>
            <p className="leading-relaxed">
              {mode === 'save'
                ? `Lowering retention to ${days} day${days === 1 ? '' : 's'} means the following older than ${cutoffText} will be permanently deleted:`
                : `The following older than ${cutoffText} (${days}-day retention) will be permanently deleted:`}
            </p>
            <ul
              className="space-y-1.5 bg-dark-950 border border-dark-800 rounded-lg p-3 text-[11px]"
              data-testid="prune-preview-list"
            >
              {rows.map((r) => (
                <li key={r.label} className="flex items-center justify-between gap-3">
                  <span className="text-slate-400">{r.label}</span>
                  <span className="text-slate-100 font-mono">{formatCount(r.count)}</span>
                </li>
              ))}
            </ul>
            <p className="text-[11px] text-slate-500 leading-relaxed">
              Counts are taken right now and may differ slightly when the deletion runs.
            </p>
          </>
        )}

        <div className="flex flex-wrap items-center justify-end gap-2 pt-2 border-t border-dark-800">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded-lg transition cursor-pointer disabled:cursor-not-allowed"
          >
            {nothingToDelete && mode === 'purge' ? 'Close' : 'Cancel'}
          </button>
          {mode === 'save' && (
            <button
              type="button"
              onClick={onDeleteLater}
              disabled={busy}
              title={`Saves the new retention. The next daily cleanup runs ${describeNextCleanup(preview.next_cleanup_at)}.`}
              className="px-3 py-1.5 text-xs bg-dark-800 hover:bg-dark-700 text-slate-200 border border-dark-600 font-medium rounded-lg transition flex items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed"
            >
              <Clock className="w-3.5 h-3.5" />
              <span>Delete at Next Daily Cleanup</span>
            </button>
          )}
          {!nothingToDelete && (
            <button
              type="button"
              onClick={onDeleteNow}
              disabled={busy}
              className="px-4 py-1.5 text-xs bg-red-700 hover:bg-red-600 text-white font-medium rounded-lg shadow-sm transition flex items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed"
            >
              {busy ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
              <span>{mode === 'save' ? 'Save and Delete Now' : 'Delete Now'}</span>
            </button>
          )}
        </div>

        {mode === 'save' && !nothingToDelete && (
          <p className="text-[11px] text-slate-500 leading-relaxed">
            The next daily cleanup runs {describeNextCleanup(preview.next_cleanup_at)}.
          </p>
        )}
      </div>
    </Modal>
  );
};
