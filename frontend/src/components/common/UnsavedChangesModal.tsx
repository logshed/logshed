import React, { useState } from 'react';
import { AlertCircle, RefreshCw, Save } from 'lucide-react';
import { Modal } from './Modal.tsx';

export interface UnsavedChangesModalProps {
  isOpen: boolean;
  onKeepEditing: () => void;
  onDiscard: () => void;
  /** Adds a "Save & Continue" button; the caller saves, then moves on only if the save worked. */
  onSave?: () => Promise<void>;
  message?: string;
  discardLabel?: string;
  zIndex?: string;
}

export const UnsavedChangesModal: React.FC<UnsavedChangesModalProps> = ({
  isOpen,
  onKeepEditing,
  onDiscard,
  onSave,
  message = 'You have unsaved changes. Closing now will discard those edits.',
  discardLabel = 'Discard Changes',
  zIndex = 'z-60',
}) => {
  const [isSaving, setIsSaving] = useState<boolean>(false);

  const handleSave = async () => {
    if (!onSave) return;
    setIsSaving(true);
    try {
      await onSave();
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onKeepEditing}
      title="Unsaved Changes"
      maxWidth="max-w-md"
      zIndex={zIndex}
    >
      <div className="space-y-4 text-xs font-sans">
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400 shrink-0">
            <AlertCircle className="w-5 h-5" />
          </div>
          <p className="text-xs text-slate-300 leading-relaxed">
            {message}
          </p>
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-end gap-2 pt-3 border-t border-dark-700">
          <button
            type="button"
            onClick={onKeepEditing}
            className="w-full sm:w-auto px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-dark-800 hover:bg-dark-750 border border-dark-700 transition cursor-pointer"
          >
            Keep Editing
          </button>
          <button
            type="button"
            onClick={onDiscard}
            className="w-full sm:w-auto px-3 py-1.5 rounded-lg text-xs font-medium text-red-400 hover:text-red-300 bg-red-950/40 hover:bg-red-900/40 border border-red-800/60 transition cursor-pointer"
          >
            {discardLabel}
          </button>
          {onSave && (
            <button
              type="button"
              disabled={isSaving}
              onClick={handleSave}
              className="w-full sm:w-auto px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-accent-700 hover:bg-accent-600 transition cursor-pointer flex items-center justify-center gap-1.5 disabled:opacity-50 shadow-xs"
            >
              {isSaving ? (
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Save className="w-3.5 h-3.5" />
              )}
              <span>Save &amp; Continue</span>
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
};
