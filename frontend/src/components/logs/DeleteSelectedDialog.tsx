import React, { useState } from 'react';
import { AlertCircle, Loader2, Trash2 } from 'lucide-react';
import { Modal } from '../common/Modal.tsx';

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
    <Modal
      isOpen
      onClose={() => !isDeleting && onClose()}
      title="Delete Selected Logs"
      maxWidth="max-w-md"
    >
      <div className="space-y-4 text-xs text-slate-200">
        <p className="leading-relaxed">
          Permanently delete <span className="font-mono font-bold text-red-400">{count}</span> selected log record{count === 1 ? '' : 's'}? This action cannot be reversed.
        </p>
        {error && (
          <div className="p-2.5 bg-red-950/60 border border-red-800 rounded text-xs text-red-300 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        <div className="flex items-center justify-end gap-2 pt-2 border-t border-dark-800">
          <button
            type="button"
            onClick={onClose}
            disabled={isDeleting}
            className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 hover:bg-dark-800 rounded transition cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleDelete}
            disabled={isDeleting}
            className="px-4 py-1.5 text-xs bg-red-600 hover:bg-red-500 text-white font-medium rounded shadow-xs transition flex items-center gap-1.5 cursor-pointer disabled:cursor-not-allowed"
          >
            {isDeleting ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>Deleting...</span>
              </>
            ) : (
              <>
                <Trash2 className="w-3.5 h-3.5" />
                <span>Confirm Delete</span>
              </>
            )}
          </button>
        </div>
      </div>
    </Modal>
  );
};
