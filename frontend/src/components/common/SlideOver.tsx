import React, { useId, useRef } from 'react';
import { X } from 'lucide-react';
import { IconButton } from './Button.tsx';
import { useDialog } from '../../utils/dialogStack.ts';

interface SlideOverProps {
  isOpen: boolean;
  onClose: () => void;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  headerActions?: React.ReactNode;
  children: React.ReactNode;
  width?: string;
}

export const SlideOver: React.FC<SlideOverProps> = ({
  isOpen,
  onClose,
  title,
  subtitle,
  headerActions,
  children,
  width = 'max-w-3xl',
}) => {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialog(isOpen, onClose, panelRef);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Backdrop overlay */}
      <div
        data-testid="slideover-backdrop"
        className="fixed inset-0 bg-backdrop/60 backdrop-blur-xs transition-opacity cursor-pointer"
        onClick={onClose}
        aria-hidden="true"
      />
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="fixed inset-y-0 right-0 flex max-w-full pl-0 sm:pl-10 pointer-events-none">
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            tabIndex={-1}
            className={`pointer-events-auto w-screen ${width} bg-surface border-l border-line shadow-2xl flex flex-col overscroll-contain outline-hidden overflow-x-hidden`}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-line bg-canvas shrink-0 gap-2">
              <div className="min-w-0 flex-1">
                <h2 id={titleId} className="text-sm font-semibold text-fg tracking-wide truncate">{title}</h2>
                {subtitle && (
                  <div className="mt-0.5">{subtitle}</div>
                )}
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {headerActions}
                <IconButton label="Close panel" size="lg" onClick={onClose}>
                  <X className="w-5 h-5 sm:w-4 sm:h-4" />
                </IconButton>
              </div>
            </div>

            {/* Body */}
            <div className="p-4 overflow-y-auto flex-1 overscroll-contain overflow-x-hidden">{children}</div>
          </div>
        </div>
      </div>
    </div>
  );
};
