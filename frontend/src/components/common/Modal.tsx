import React, { useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
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
    <div className={`fixed inset-0 ${zIndex} flex items-center justify-center p-3 sm:p-4 bg-black/75 backdrop-blur-xs`}>
      <div
        className={`bg-dark-900 border border-dark-700 rounded-lg shadow-2xl w-full ${maxWidth} max-h-[calc(100dvh-2rem)] sm:max-h-[90dvh] flex flex-col overscroll-contain outline-none animate-in fade-in zoom-in-95 duration-150`}
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-dark-700 bg-dark-950 rounded-t-lg">
          <h2 id={titleId} className="text-sm font-semibold text-slate-200 tracking-wide">{title}</h2>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-200 hover:bg-dark-800 p-1 rounded transition"
            aria-label="Close dialog"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-4 overflow-y-auto flex-1">{children}</div>
      </div>
    </div>,
    document.body
  );
};
