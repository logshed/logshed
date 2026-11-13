import React, { useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { IconButton } from './Button.tsx';
import { useDialog } from '../../utils/dialogStack.ts';

interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  maxWidth?: string;
  zIndex?: string;
}

export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  children,
  maxWidth = 'max-w-2xl',
  zIndex = 'z-50',
}) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialog(isOpen, onClose, dialogRef);

  if (!isOpen) return null;

  return createPortal(
    <div className={`fixed inset-0 ${zIndex} flex items-center justify-center p-3 sm:p-4 bg-backdrop/75 backdrop-blur-xs`}>
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
          <h2 id={titleId} className="text-sm font-semibold text-fg tracking-wide">{title}</h2>
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
