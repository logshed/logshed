import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { Modal } from './Modal.tsx';

// Two looks shared by several dialogs. Dialogs with a look of their own pass the class props;
// those props go once the dialogs share one button style.
const LOOKS = {
  // A question in plain text, with a divider above the buttons
  plain: {
    body: 'space-y-4 text-xs',
    footer: 'pt-3 border-t border-dark-700 flex justify-end gap-2',
    cancel:
      'px-3.5 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-dark-800 transition cursor-pointer',
    confirm:
      'px-3.5 py-1.5 rounded-lg text-xs font-medium text-white bg-red-600 hover:bg-red-500 transition cursor-pointer',
  },
  // The question and details inside a red warning box
  notice: {
    body: 'space-y-4 text-xs font-sans',
    footer: 'flex items-center justify-end gap-2 pt-2',
    cancel:
      'px-3 py-1.5 text-xs bg-dark-800 hover:bg-dark-700 text-slate-300 border border-dark-600 rounded-lg transition cursor-pointer',
    confirm:
      'flex items-center gap-1.5 px-3.5 py-1.5 text-xs bg-red-600 hover:bg-red-500 text-white font-medium rounded-lg shadow-sm transition cursor-pointer',
  },
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
  /** An icon before the label; the label is then wrapped in a span. */
  confirmIcon?: React.ReactNode;
  cancelLabel?: string;
  confirmDisabled?: boolean;
  cancelDisabled?: boolean;
  look?: keyof typeof LOOKS;
  /** Classes of the column inside the notice look's warning box. */
  noticeClassName?: string;
  bodyClassName?: string;
  footerClassName?: string;
  cancelClassName?: string;
  confirmClassName?: string;
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
  noticeClassName = 'space-y-2 flex-1',
  bodyClassName,
  footerClassName,
  cancelClassName,
  confirmClassName,
  maxWidth = 'max-w-md',
  children,
}) => {
  const styles = LOOKS[look];
  return (
    <Modal isOpen={isOpen} onClose={onClose ?? onCancel} title={title} maxWidth={maxWidth}>
      <div className={bodyClassName ?? styles.body}>
        {look === 'notice' ? (
          <div className="flex items-start gap-3 p-3 bg-red-950/30 border border-red-800/60 rounded-lg text-slate-200">
            <AlertTriangle className="w-5 h-5 text-red-400 shrink-0 mt-0.5" />
            <div className={noticeClassName}>{children}</div>
          </div>
        ) : (
          children
        )}

        <div className={footerClassName ?? styles.footer}>
          <button
            type="button"
            onClick={onCancel}
            disabled={cancelDisabled}
            className={cancelClassName ?? styles.cancel}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={confirmDisabled}
            className={confirmClassName ?? styles.confirm}
          >
            {confirmIcon ? (
              <>
                {confirmIcon}
                <span>{confirmLabel}</span>
              </>
            ) : (
              confirmLabel
            )}
          </button>
        </div>
      </div>
    </Modal>
  );
};
