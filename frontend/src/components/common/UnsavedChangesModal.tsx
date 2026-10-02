import React from 'react';
import { AlertCircle } from 'lucide-react';
import { Modal } from './Modal.tsx';

export interface UnsavedChangesModalProps {
  isOpen: boolean;
  onKeepEditing: () => void;
  onDiscard: () => void;
  message?: string;
  zIndex?: string;
}

export const UnsavedChangesModal: React.FC<UnsavedChangesModalProps> = ({
  isOpen,
  onKeepEditing,
  onDiscard,
  message = 'You have unsaved changes. Closing now will discard those edits.',
  zIndex = 'z-[60]',
}) => {
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
            Discard Changes
          </button>
        </div>
      </div>
    </Modal>
  );
};
