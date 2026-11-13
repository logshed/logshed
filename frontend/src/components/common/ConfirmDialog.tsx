import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { Modal } from './Modal.tsx';
import { Button } from './Button.tsx';

// Two looks: a question in plain text with a divider above the buttons, or the question and its details
// inside a red warning box.
const LOOKS = {
  plain: { body: 'space-y-4 text-xs text-fg-soft', footer: 'pt-3 border-t border-line flex justify-end gap-2' },
  notice: { body: 'space-y-4 text-xs font-sans', footer: 'flex items-center justify-end gap-2 pt-2' },
} as const;

export interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  /** The Cancel button; also Esc, the close button and the backdrop unless onClose is given. */
  onCancel: () => void;
  onClose?: () => void;
  onConfirm: (e: React.MouseEvent<HTMLButtonElement>) => void;
  /** The confirm button's text, or its whole content when it changes while busy. */
  confirmLabel: React.ReactNode;
  /** An icon before the label. */
  confirmIcon?: React.ReactNode;
  cancelLabel?: string;
  confirmDisabled?: boolean;
  cancelDisabled?: boolean;
  look?: keyof typeof LOOKS;
  /** danger for a delete or another change that cannot be undone; primary for any other action. */
  confirmVariant?: 'danger' | 'primary';
  /** Layout classes of the column inside the notice look's warning box. */
  noticeClassName?: string;
  maxWidth?: string;
  children: React.ReactNode;
}

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  isOpen,
  title,
  onCancel,
  onClose,
  onConfirm,
  confirmLabel,
  confirmIcon,
  cancelLabel = 'Cancel',
  confirmDisabled,
  cancelDisabled,
  look = 'plain',
  confirmVariant = 'danger',
  noticeClassName = 'space-y-2 flex-1',
  maxWidth = 'max-w-md',
  children,
}) => {
  const styles = LOOKS[look];
  return (
    <Modal isOpen={isOpen} onClose={onClose ?? onCancel} title={title} maxWidth={maxWidth}>
      <div className={styles.body}>
        {look === 'notice' ? (
          <div className="flex items-start gap-3 p-3 bg-danger-950/30 border border-danger-800/60 rounded-lg text-fg">
            <AlertTriangle className="w-5 h-5 text-danger-400 shrink-0 mt-0.5" />
            <div className={noticeClassName}>{children}</div>
          </div>
        ) : (
          children
        )}

        <div className={styles.footer}>
          <Button onClick={onCancel} disabled={cancelDisabled}>
            {cancelLabel}
          </Button>
          <Button variant={confirmVariant} onClick={onConfirm} disabled={confirmDisabled} icon={confirmIcon}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
};
