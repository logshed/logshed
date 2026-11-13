import React, { useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { IconButton } from './Button.tsx';
import { useDialog } from '../../utils/dialogStack.ts';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  /** An icon before the title, sized here. */
  icon?: React.ReactElement<{ className?: string }>;
  children: React.ReactNode;
  maxWidth?: string;
  zIndex?: string;
  /** False for a dialog that must be ended by a button, such as one showing a secret only once. */
  closeOnEscape?: boolean;
  testId?: string;
}

export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  icon,
  children,
  maxWidth = 'max-w-2xl',
  zIndex = 'z-50',
  closeOnEscape = true,
  testId,
}) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialog(isOpen, onClose, dialogRef, { closeOnEscape });

  if (!isOpen) return null;

  return createPortal(
    <div data-testid={testId} className={`fixed inset-0 ${zIndex} flex items-center justify-center p-3 sm:p-4 bg-backdrop/75 backdrop-blur-xs`}>
      <div
        className={`bg-surface border border-line rounded-lg shadow-2xl w-full ${maxWidth} max-h-[calc(100dvh-2rem)] sm:max-h-[90dvh] flex flex-col overscroll-contain outline-hidden`}
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-line bg-canvas rounded-t-lg">
          <h2 id={titleId} className="text-sm font-semibold text-fg tracking-wide flex items-center gap-2">
            {icon && React.cloneElement(icon, { className: 'w-4 h-4 text-accent-500 shrink-0' })}
            <span>{title}</span>
          </h2>
          <IconButton label="Close dialog" size="sm" onClick={onClose}>
            <X className="w-4 h-4" />
          </IconButton>
        </div>

        {/* Body */}
        <div className="p-4 overflow-y-auto flex-1">{children}</div>
      </div>
    </div>,
    document.body
  );
};
