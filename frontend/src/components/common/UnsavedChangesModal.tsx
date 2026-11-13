import React, { useState } from 'react';
import { AlertCircle, RefreshCw, Save } from 'lucide-react';
import { Modal } from './Modal.tsx';
import { Button } from './Button.tsx';

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
          <div className="p-2 rounded-lg bg-warning-500/10 border border-warning-500/20 text-warning-400 shrink-0">
            <AlertCircle className="w-5 h-5" />
          </div>
          <p className="text-xs text-fg-soft leading-relaxed">
            {message}
          </p>
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-end gap-2 pt-3 border-t border-line">
          <Button onClick={onKeepEditing} className="w-full sm:w-auto">
            Keep Editing
          </Button>
          <Button variant="danger-soft" onClick={onDiscard} className="w-full sm:w-auto">
            {discardLabel}
          </Button>
          {onSave && (
            <Button
              variant="primary"
              disabled={isSaving}
              onClick={handleSave}
              className="w-full sm:w-auto"
              icon={isSaving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            >
              Save &amp; Continue
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
};
