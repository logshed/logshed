import React, { useState } from 'react';
import { AlertCircle, Loader2, Trash2 } from 'lucide-react';
import { ConfirmDialog } from '../common/ConfirmDialog.tsx';

interface DeleteSelectedDialogProps {
  count: number;
  onClose: () => void;
  /** Deletes the selected logs; a rejection shows its message in the dialog */
  onDelete: () => Promise<void>;
}

/** Confirms deleting the ticked logs, and shows the request's progress and any error. */
export const DeleteSelectedDialog: React.FC<DeleteSelectedDialogProps> = ({ count, onClose, onDelete }) => {
  const [isDeleting, setIsDeleting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const handleDelete = async () => {
    if (count === 0) return;
    try {
      setIsDeleting(true);
      setError(null);
      await onDelete();
    } catch (err: any) {
      setError(err.message || 'Failed to delete selected logs.');
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <ConfirmDialog
      isOpen
      title="Delete Selected Logs"
      onCancel={onClose}
      onClose={() => !isDeleting && onClose()}
      onConfirm={handleDelete}
      cancelDisabled={isDeleting}
      confirmDisabled={isDeleting}
      confirmIcon={isDeleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
      confirmLabel={isDeleting ? 'Deleting...' : 'Confirm Delete'}
      bodyClassName="space-y-4 text-xs text-slate-200"
      footerClassName="flex items-center justify-end gap-2 pt-2 border-t border-dark-800"
      cancelClassName="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded-sm transition cursor-pointer"
      confirmClassName="px-4 py-1.5 text-xs bg-red-600 hover:bg-red-500 text-white font-medium rounded-sm shadow-xs transition flex items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed"
    >
      <p className="leading-relaxed">
        Permanently delete <span className="font-mono font-bold text-red-400">{count}</span> selected log record{count === 1 ? '' : 's'}? This action cannot be reversed.
      </p>
      {error && (
        <div className="p-2.5 bg-red-950/60 border border-red-800 rounded-sm text-xs text-red-300 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}
    </ConfirmDialog>
  );
};
